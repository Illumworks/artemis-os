"""M5 — Marketing signal → memory observation tests.

Verifies that qualifying a signal writes:
  1. A memory_drawers row (verbatim evidence) in scope workspace:marketing.
  2. A memory_observations row (curated summary) in the same scope.
  3. memory_evidence rows linking the observation to both the drawer and the
     signal_queue row.

Non-qualified transitions (rejected_hard_filter, suppressed_stale) must NOT
write any memory rows.

Failure isolation: write_drawer raising must not break the signal_queue update.

Uses the tools conftest db_session + local memory-table truncation.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

import artemis.db as _db
import artemis.marketing.models  # noqa: F401
import artemis.memory.models  # noqa: F401 — ensures memory tables on Base.metadata
import artemis.pipelines.models  # noqa: F401
import artemis.tools.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.marketing.models import SignalQueue
from artemis.memory.models import MemoryDrawer, MemoryEvidence, MemoryObservation, MemoryScope
from artemis.tools.context import ToolContext
from artemis.tools.signal_queue_ops import _update_status_factory

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_QUALIFIER = "marketing.qualifier.cross_reference"

# Combined truncate: marketing + memory tables, respecting FK order.
_TRUNCATE_SQL = text(
    "TRUNCATE "
    # Memory graph (depend on entities + observations)
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    # Memory core
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs, "
    # Marketing + pipeline
    "tool_invocations, "
    "campaign_state_transitions, approvals, campaign_deliverables, "
    "content_asset_links, content_assets, campaign_briefs, campaign_candidates, "
    "scout_runs, qualifier_rule_applications, skipped_signals, signal_queue, "
    "rulesets, territory_config, signal_reason_codes, "
    "pipeline_ai_conversations, pipeline_runs, pipelines, "
    "agent_context, agent_run_trajectory_summaries, definition_proposals, agent_runs, "
    "agent_skills, workflow_runs, agents, skills, workflows, agent_chains, agent_dags, "
    "builder_sessions "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session with both marketing + memory tables truncated."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    # Also patch the module-level SessionLocal so fresh-session reads work.
    original_engine = _db.engine
    original_session_local = _db.SessionLocal
    _db.engine = engine
    _db.SessionLocal = __import__(
        "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
    ).async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
            # Explicitly rollback any pending autobegin transaction so locks
            # are released before the next test's TRUNCATE runs. Without this,
            # the embedding INSERT lock on memory_embeddings can deadlock with
            # the next fixture's TRUNCATE (which holds AccessExclusiveLock on
            # memory_scopes while waiting for memory_embeddings).
            if session.in_transaction():
                await session.rollback()
    finally:
        _db.engine = original_engine
        _db.SessionLocal = original_session_local
        await engine.dispose()


def _ctx(session: AsyncSession, agent_id: str = _QUALIFIER) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-m5-test",
        pipeline_run_id="pipe-run-001",
    )


async def _seed_signal(
    session: AsyncSession,
    *,
    headline: str = "District seeks new literacy vendor",
    district_id: str | None = "TX-001",
    reason_codes: list[Any] | None = None,
) -> int:
    # No pipeline_run_id — FK constraint requires a real pipeline_runs row.
    row = SignalQueue(
        source_type="news_article",
        headline=headline,
        summary="Board approved an RFP.",
        source_url="https://example.com/article",
        campaign_family="obc",
        urgency_tier="standard",
        discovered_by="regional_news",
        district_id=district_id,
        state="TX",
        reason_codes=reason_codes
        if reason_codes is not None
        else [{"code": "VENDOR_DISSATISFACTION"}],
        signal_status="pending_qualification",
    )
    session.add(row)
    await session.flush()
    return row.id


# ── Test #1: Qualified transition writes drawer + observation + evidence ──────


@pytest.mark.asyncio
async def test_qualified_transition_writes_drawer_observation_evidence(
    db_session: AsyncSession,
) -> None:
    """pending_qualification → qualified writes drawer, observation, and evidence chain."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session))
    result = await impl({"signalId": signal_id, "newStatus": "qualified"})
    data = json.loads(result)
    assert data["signal_status"] == "qualified"

    # Drawer written in workspace:marketing scope
    drawers = (
        (
            await db_session.execute(
                select(MemoryDrawer).where(
                    MemoryDrawer.scope_kind == "workspace",
                    MemoryDrawer.scope_id == "marketing",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(drawers) == 1
    drawer = drawers[0]
    assert drawer.source_kind == "signal_queue"
    assert drawer.source_id == str(signal_id)

    # Observation written in same scope
    observations = (
        (
            await db_session.execute(
                select(MemoryObservation).where(
                    MemoryObservation.scope_kind == "workspace",
                    MemoryObservation.scope_id == "marketing",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(observations) == 1
    obs = observations[0]
    assert str(signal_id) in obs.content
    assert "District seeks new literacy vendor" in obs.content
    assert obs.category == "signal_qualification"

    # Evidence: two rows — one drawer link, one signal_queue link
    evidence_rows = (
        (
            await db_session.execute(
                select(MemoryEvidence).where(MemoryEvidence.observation_id == obs.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(evidence_rows) == 2
    source_kinds = {e.source_kind for e in evidence_rows}
    assert "drawer" in source_kinds
    assert "signal_queue" in source_kinds


# ── Test #2: Non-qualified transitions do NOT write memory ───────────────────


@pytest.mark.asyncio
async def test_non_qualified_transitions_do_not_write_memory(
    db_session: AsyncSession,
) -> None:
    """rejected_hard_filter transition must not create any memory rows."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session))
    result = await impl({"signalId": signal_id, "newStatus": "rejected_hard_filter"})
    data = json.loads(result)
    assert data["signal_status"] == "rejected_hard_filter"

    drawer_count = len((await db_session.execute(select(MemoryDrawer))).scalars().all())
    obs_count = len((await db_session.execute(select(MemoryObservation))).scalars().all())
    assert drawer_count == 0
    assert obs_count == 0


# ── Test #3: Idempotency (duplicate qualify attempt) ─────────────────────────


@pytest.mark.asyncio
async def test_qualify_idempotent_one_drawer_one_observation(
    db_session: AsyncSession,
) -> None:
    """Content-hash deduplication: calling _write_signal_memory twice yields exactly
    one drawer + one observation (ON CONFLICT DO NOTHING on both tables).

    We bypass the state machine here because it would block the second transition;
    idempotency is a property of write_drawer / write_observation, not update_status.
    """
    from artemis.tools.signal_queue_ops import _write_signal_memory

    signal_id = await _seed_signal(db_session)
    row = await db_session.get(SignalQueue, signal_id)
    assert row is not None

    # First write
    await _write_signal_memory(db_session, row)
    # Second write — same content hash, should be no-ops
    await _write_signal_memory(db_session, row)

    drawers = (await db_session.execute(select(MemoryDrawer))).scalars().all()
    observations = (await db_session.execute(select(MemoryObservation))).scalars().all()
    assert len(drawers) == 1
    assert len(observations) == 1


# ── Test #4: Scope auto-creation ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scope_auto_created_on_qualify(db_session: AsyncSession) -> None:
    """No pre-existing workspace:marketing scope — qualifying a signal creates it exactly once."""
    # Confirm no scope exists before the test
    scope_before = (
        await db_session.execute(
            select(MemoryScope).where(
                MemoryScope.scope_kind == "workspace",
                MemoryScope.scope_id == "marketing",
            )
        )
    ).scalar_one_or_none()
    assert scope_before is None

    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session))
    await impl({"signalId": signal_id, "newStatus": "qualified"})

    scopes = (
        (
            await db_session.execute(
                select(MemoryScope).where(
                    MemoryScope.scope_kind == "workspace",
                    MemoryScope.scope_id == "marketing",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(scopes) == 1


# ── Test #5: Failure isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_write_failure_does_not_break_signal_queue_update(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """write_drawer raising must not prevent signal_queue status from becoming qualified."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session))

    with (
        patch(
            "artemis.tools.signal_queue_ops.write_drawer",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated drawer failure"),
        ),
        caplog.at_level(logging.WARNING, logger="artemis.tools.signal_queue_ops"),
    ):
        result = await impl({"signalId": signal_id, "newStatus": "qualified"})

    # (a) signal_queue status is still qualified
    data = json.loads(result)
    assert data["signal_status"] == "qualified"

    # (b) function returned normally (no raise)

    # (c) warning logged
    assert any("M5 memory write failed" in r.message for r in caplog.records)
    assert any(str(signal_id) in r.message for r in caplog.records)


# ── Test #6: Provenance verification ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_provenance_evidence_chain_drawer_and_signal_queue(
    db_session: AsyncSession,
) -> None:
    """Observation evidence list contains both a drawer link and a signal_queue link."""
    signal_id = await _seed_signal(db_session)
    _, impl = _update_status_factory(_ctx(db_session))
    await impl({"signalId": signal_id, "newStatus": "qualified"})

    obs = (
        await db_session.execute(
            select(MemoryObservation).where(
                MemoryObservation.scope_kind == "workspace",
                MemoryObservation.scope_id == "marketing",
            )
        )
    ).scalar_one()

    evidence_rows = (
        (
            await db_session.execute(
                select(MemoryEvidence).where(MemoryEvidence.observation_id == obs.id)
            )
        )
        .scalars()
        .all()
    )
    kinds = {e.source_kind for e in evidence_rows}
    assert "drawer" in kinds, f"Expected 'drawer' in evidence source_kinds, got {kinds}"
    assert "signal_queue" in kinds, f"Expected 'signal_queue' in evidence source_kinds, got {kinds}"

    # Signal queue link points to the right signal
    sq_link = next(e for e in evidence_rows if e.source_kind == "signal_queue")
    assert sq_link.source_id == signal_id
