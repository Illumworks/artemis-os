"""P3-C12: Rejection reason carryover to memory observations.

Tests for the write path that captures the reject reason and writes it into
memory observations scoped to the responsible agent (C steps 1+2).

Surfaces covered:
  C1/C2 — signal reject via write_signal_gate1_approval_observation
           (with rejection_reason + agent_slug parameters)
  C1/C2 — content_draft reject via write_pipeline_gate_decision_observation
           (with rejection_reason + agent_slug parameters)
  C1/C2 — pipeline resume WITH reason via write_pipeline_gate_decision_observation
  backward compat — callers without new params still work
  failure isolation — memory write failure does not break routes

Requires ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401
import artemis.builders.models  # noqa: F401
import artemis.db as _db
import artemis.memory.models  # noqa: F401
import artemis.tools.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryEvidence, MemoryObservation, MemoryObservationScope

# ── DB URL guard ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

# ── Truncation SQL ─────────────────────────────────────────────────────────────

_TRUNCATE_SQL = text(
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observation_scopes, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session; patches artemis.db.SessionLocal so carryover opens its
    session on the same test engine."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
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
            if session.in_transaction():
                await session.rollback()
    finally:
        _db.engine = original_engine
        _db.SessionLocal = original_session_local
        await engine.dispose()


# ── Shared assertion helpers ──────────────────────────────────────────────────


async def _get_obs_and_scopes(
    session: AsyncSession,
) -> tuple[list[MemoryObservation], list[MemoryObservationScope]]:
    obs = (await session.execute(select(MemoryObservation))).scalars().all()
    scopes = (await session.execute(select(MemoryObservationScope))).scalars().all()
    return list(obs), list(scopes)


# ═══════════════════════════════════════════════════════════════════════════
# Test 1 — Signal reject WITH reason → observation includes reason + agent scope
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_signal_reject_with_reason_includes_reason_and_agent_scope(
    db_session: AsyncSession,
) -> None:
    """Signal reject with reason → observation content has 'Reason:' + agent scope."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=101,
        new_status="rejected_at_gate_1",
        decided_by="operator",
        decision_payload={"headline": "Ohio legislation update"},
        rejection_reason="off-territory",
        agent_slug="marketing.qualifier.cross_reference",
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1, f"Expected 1 observation, got {len(obs_list)}"

    obs = obs_list[0]
    assert obs.category == "signal_gate1_decision"
    assert "rejected" in obs.content
    assert "Reason: off-territory" in obs.content

    # Primary scope: workspace:marketing; secondary: workspace:platform + agent:qualifier
    assert len(scope_rows) == 3, f"Expected 3 scope-join rows, got {len(scope_rows)}"
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("workspace", "marketing", True) in scope_pairs, (
        f"workspace:marketing primary missing from {scope_pairs}"
    )
    assert ("workspace", "platform", False) in scope_pairs
    assert ("agent", "marketing.qualifier.cross_reference", False) in scope_pairs, (
        f"agent scope missing from {scope_pairs}"
    )

    # Evidence
    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(ev_rows) == 1
    assert ev_rows[0].source_kind == "signal_queue"
    assert ev_rows[0].source_id == "101"


# ═══════════════════════════════════════════════════════════════════════════
# Test 2 — Signal reject WITHOUT reason → observation lands, no "Reason:" clause
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_signal_reject_without_reason_no_reason_clause(
    db_session: AsyncSession,
) -> None:
    """Signal reject without reason → observation still lands; no 'Reason:' clause."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=102,
        new_status="rejected_at_gate_1",
        decided_by="operator",
        decision_payload=None,
        rejection_reason=None,
        agent_slug="marketing.qualifier.cross_reference",
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    assert "Reason:" not in obs_list[0].content

    # Still has agent scope even without a reason
    scope_pairs = {(r.scope_kind, r.scope_id) for r in scope_rows}
    assert ("agent", "marketing.qualifier.cross_reference") in scope_pairs


# ═══════════════════════════════════════════════════════════════════════════
# Test 3 — Content draft reject via MC4 with reason → agent scope + reason content
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_content_draft_reject_with_agent_scope_and_reason(
    db_session: AsyncSession,
) -> None:
    """Content draft reject → MC4 observation has agent scope + reason in content."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-content-01",
        pipeline_id="marketing-pipeline",
        node_id="gate_content_review",
        decision="rejected",
        decided_by="operator",
        decision_payload={"pipeline_name": "Marketing Pipeline"},
        rejection_reason="draft tone off-brand",
        agent_slug="marketing.content.writing_studio_adapter",
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    obs = obs_list[0]
    assert obs.category == "pipeline_gate_decision"
    # Primary scope: pipeline:<id>
    assert obs.scope_kind == "pipeline"
    assert obs.scope_id == "marketing-pipeline"
    assert "Reason: draft tone off-brand" in obs.content

    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("pipeline", "marketing-pipeline", True) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs
    assert ("agent", "marketing.content.writing_studio_adapter", False) in scope_pairs, (
        f"agent scope missing from {scope_pairs}"
    )

    ev_rows = (await db_session.execute(select(MemoryEvidence))).scalars().all()
    assert len(ev_rows) == 1
    assert ev_rows[0].source_kind == "pipeline_run"
    assert ev_rows[0].source_id == "run-content-01"


# ═══════════════════════════════════════════════════════════════════════════
# Test 4 — Pipeline resume WITH reason → MC4 observation includes reason + agent scope
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pipeline_gate_reject_with_reason_includes_agent_scope(
    db_session: AsyncSession,
) -> None:
    """Pipeline gate decision with reason + agent_slug → observation includes both."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-gate-001",
        pipeline_id="pipeline-qual-v2",
        node_id="gate_node_a",
        decision="rejected",
        decided_by="jon@amiralearning.com",
        decision_payload={"pipeline_name": "Qualifier Pipeline v2"},
        rejection_reason="signal does not meet ICP criteria",
        agent_slug="marketing.qualifier.cross_reference",
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    obs = obs_list[0]
    assert "Reason: signal does not meet ICP criteria" in obs.content

    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("agent", "marketing.qualifier.cross_reference", False) in scope_pairs


# ═══════════════════════════════════════════════════════════════════════════
# Test 5 — Approve paths write observation with no reason clause, no agent scope
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approve_path_no_reason_clause(db_session: AsyncSession) -> None:
    """Approve: observation lands with existing shape; no Reason clause."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=200,
        new_status="approved",
        decided_by="operator",
        decision_payload={"headline": "Big school district bill"},
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    obs = obs_list[0]
    assert "Reason:" not in obs.content
    assert "approved" in obs.content

    # No agent scope when not passed
    scope_pairs = {(r.scope_kind, r.scope_id) for r in scope_rows}
    assert ("agent", "marketing.qualifier.cross_reference") not in scope_pairs


@pytest.mark.asyncio
async def test_mc4_approve_no_reason_clause(db_session: AsyncSession) -> None:
    """MC4 approve: no Reason clause even when rejection_reason is None."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-approve-01",
        pipeline_id="pipeline-approved",
        node_id="gate_approved",
        decision="approved",
        decided_by="operator",
        decision_payload={"pipeline_name": "My Pipeline"},
        rejection_reason=None,
        agent_slug=None,
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    assert "Reason:" not in obs_list[0].content
    # Only 2 scope rows: pipeline + platform (no agent scope when agent_slug=None)
    assert len(scope_rows) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Test 6 — Failure isolation: write failure does not break the route
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc2_rejection_reason_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure in _multi_scope_observation_write is isolated; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated rejection-carryover failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        await write_signal_gate1_approval_observation(
            signal_id=999,
            new_status="rejected_at_gate_1",
            decided_by="operator",
            decision_payload=None,
            rejection_reason="test failure isolation",
            agent_slug="marketing.qualifier.cross_reference",
        )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 0, "Memory write should have been no-op on failure"

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC2 memory observation write failed" in m for m in warnings), (
        f"Expected MC2 warning. Got: {warnings}"
    )


@pytest.mark.asyncio
async def test_mc4_rejection_reason_failure_isolation(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Failure in MC4 write is isolated; warning is logged."""
    import artemis.builder.memory_carryover as _carryover
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated MC4 failure")

    monkeypatch.setattr(_carryover, "_multi_scope_observation_write", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.memory_carryover"):
        await write_pipeline_gate_decision_observation(
            pipeline_run_id="run-fail-01",
            pipeline_id="pipe-fail",
            node_id="gate_fail",
            decision="rejected",
            decided_by="operator",
            decision_payload=None,
            rejection_reason="test failure isolation",
            agent_slug="marketing.content.writing_studio_adapter",
        )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 0

    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("MC4 memory observation write failed" in m for m in warnings), (
        f"Expected MC4 warning. Got: {warnings}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Test 7 — Backward compat: callers without new params still produce valid observations
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc2_backward_compat_no_new_params(db_session: AsyncSession) -> None:
    """Existing MC2 callers without rejection_reason or agent_slug still work."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    await write_signal_gate1_approval_observation(
        signal_id=50,
        new_status="approved",
        decided_by="operator",
        decision_payload={"headline": "Compat test headline"},
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1, "Backward-compat call should still write 1 observation"
    obs = obs_list[0]
    assert obs.category == "signal_gate1_decision"
    assert obs.scope_kind == "workspace"
    assert obs.scope_id == "marketing"

    # Old shape: exactly 2 scope rows (marketing + platform), no agent row
    assert len(scope_rows) == 2, (
        f"Backward-compat call should produce 2 scope rows, got {len(scope_rows)}"
    )
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("workspace", "marketing", True) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs


@pytest.mark.asyncio
async def test_mc4_backward_compat_no_new_params(db_session: AsyncSession) -> None:
    """Existing MC4 callers without rejection_reason or agent_slug still work."""
    from artemis.builder.memory_carryover import write_pipeline_gate_decision_observation

    await write_pipeline_gate_decision_observation(
        pipeline_run_id="run-compat-01",
        pipeline_id="pipeline-compat",
        node_id="gate_compat",
        decision="approved",
        decided_by="operator",
        decision_payload={"pipeline_name": "Compat Pipeline"},
    )

    obs_list, scope_rows = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    obs = obs_list[0]
    assert obs.category == "pipeline_gate_decision"
    assert obs.scope_kind == "pipeline"
    assert obs.scope_id == "pipeline-compat"

    # Old shape: exactly 2 scope rows (pipeline + platform), no agent row
    assert len(scope_rows) == 2
    scope_pairs = {(r.scope_kind, r.scope_id, r.is_primary) for r in scope_rows}
    assert ("pipeline", "pipeline-compat", True) in scope_pairs
    assert ("workspace", "platform", False) in scope_pairs


# ═══════════════════════════════════════════════════════════════════════════
# Test 8 — Approve path: rejection_reason is NOT appended on approval
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_mc2_approve_rejection_reason_not_appended(db_session: AsyncSession) -> None:
    """When decision is 'approved', rejection_reason is not appended even if provided."""
    from artemis.builder.memory_carryover import write_signal_gate1_approval_observation

    # This is a defensive check: caller should not pass rejection_reason on approve,
    # but if they do the observation content must NOT include it.
    await write_signal_gate1_approval_observation(
        signal_id=300,
        new_status="approved",
        decided_by="operator",
        decision_payload={"headline": "Ohio bill"},
        rejection_reason="this should not appear",
        agent_slug="marketing.qualifier.cross_reference",
    )

    obs_list, _ = await _get_obs_and_scopes(db_session)
    assert len(obs_list) == 1
    content = obs_list[0].content
    assert "this should not appear" not in content
    assert "Reason:" not in content


# ═══════════════════════════════════════════════════════════════════════════
# Test 9 — _resolve_upstream_agent_slug helper
# ═══════════════════════════════════════════════════════════════════════════


def test_resolve_upstream_agent_slug_finds_agent() -> None:
    """_resolve_upstream_agent_slug returns agent_id from nearest upstream agent node."""
    from artemis.pipelines.routes import _resolve_upstream_agent_slug

    nodes = [
        {"id": "trigger_1", "type": "trigger_event", "config": {}},
        {
            "id": "agent_1",
            "type": "agent_invocation",
            "config": {"agent_id": "marketing.qualifier.cross_reference"},
        },
        {"id": "gate_1", "type": "human_gate", "config": {}},
    ]
    edges = [
        {"source_node_id": "trigger_1", "target_node_id": "agent_1"},
        {"source_node_id": "agent_1", "target_node_id": "gate_1"},
    ]
    result = _resolve_upstream_agent_slug("gate_1", nodes, edges)
    assert result == "marketing.qualifier.cross_reference"


def test_resolve_upstream_agent_slug_returns_none_on_no_agent() -> None:
    """_resolve_upstream_agent_slug returns None when no upstream agent node."""
    from artemis.pipelines.routes import _resolve_upstream_agent_slug

    nodes = [
        {"id": "trigger_1", "type": "trigger_event", "config": {}},
        {"id": "gate_1", "type": "human_gate", "config": {}},
    ]
    edges = [
        {"source_node_id": "trigger_1", "target_node_id": "gate_1"},
    ]
    result = _resolve_upstream_agent_slug("gate_1", nodes, edges)
    assert result is None


def test_resolve_upstream_agent_slug_empty_graph() -> None:
    """_resolve_upstream_agent_slug handles empty nodes/edges gracefully."""
    from artemis.pipelines.routes import _resolve_upstream_agent_slug

    result = _resolve_upstream_agent_slug("gate_1", [], [])
    assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# Test 10 — ResumeRunRequest accepts optional reason field
# ═══════════════════════════════════════════════════════════════════════════


def test_resume_run_request_reason_is_optional() -> None:
    """ResumeRunRequest accepts optional reason field without validation errors."""
    from artemis.pipelines.routes import ResumeRunRequest

    # No reason
    r1 = ResumeRunRequest(node_id="gate_1", decision="approved", actor="operator")
    assert r1.reason is None

    # With reason
    r2 = ResumeRunRequest(
        node_id="gate_1", decision="rejected", actor="operator", reason="off-territory"
    )
    assert r2.reason == "off-territory"

    # Empty string reason is allowed (never required)
    r3 = ResumeRunRequest(node_id="gate_1", decision="rejected", actor="operator", reason="")
    assert r3.reason == ""
