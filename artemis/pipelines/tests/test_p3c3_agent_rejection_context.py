"""P3-C3: Agents read own past rejections at runtime.

Tests for the read helper (fetch_agent_rejection_context) and for
the agent_executor injection that provides prior rejection context
to qualifier and content agents.

Coverage:
  1. Basic retrieval — returns only rejection rows, parses reason, newest first.
  2. Limit honored — 10 seeded, limit=3 returns exactly 3.
  3. Wrong agent — rejections for agent A not returned when querying agent B.
  4. No observations — returns [] without error.
  5. Failure isolation — monkeypatched store raises; helper returns [] + logs warning.
  6. executor integration (qualifier agent) — prior_rejections + instruction injected.
  7. Non-target agent (scout) — keys NOT injected.
  8. Empty list — keys NOT injected when no rejections exist.

Requires ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401 — register builder models
import artemis.builders.models  # noqa: F401 — register all builder models on Base.metadata
import artemis.db as _db
import artemis.integrations.models  # noqa: F401 — FK resolution
import artemis.marketing.models  # noqa: F401 — pipeline_runs FK campaign_candidates
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.pipelines.models  # noqa: F401 — register pipeline models
import artemis.tools.models  # noqa: F401 — tool_invocations FK
from artemis.db import attach_pgvector_codec

# ── DB URL guard ──────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

pytestmark = pytest.mark.asyncio

# ── Truncation SQL ─────────────────────────────────────────────────────────────

_TRUNCATE_MEMORY = text(
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observation_scopes, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs "
    "RESTART IDENTITY CASCADE"
)

_TRUNCATE_PIPELINES = text(
    "TRUNCATE pipeline_runs, pipelines, approvals, agent_context, "
    "agent_run_trajectory_summaries, definition_proposals, "
    "agent_runs, agent_skills, agents, integrations "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session; patches artemis.db.SessionLocal so memory writes use
    the same test engine."""
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
                await session.execute(_TRUNCATE_MEMORY)
                await session.execute(_TRUNCATE_PIPELINES)
            yield session
            if session.in_transaction():
                await session.rollback()
    finally:
        _db.engine = original_engine
        _db.SessionLocal = original_session_local
        await engine.dispose()


# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_observation(
    session: AsyncSession,
    *,
    agent_id: str,
    category: str,
    content: str,
) -> int:
    """Write one observation scoped to agent:<agent_id> and return its id."""
    from artemis.memory.schemas import Scope
    from artemis.memory.store import get_or_create_scope, write_observation

    scope = Scope(scope_kind="agent", scope_id=agent_id)
    await get_or_create_scope(session, scope.scope_kind, scope.scope_id)
    obs = await write_observation(
        session,
        scope=scope,
        content=content,
        category=category,
        source_quality=0.9,
        confidence_origin="test_seed",
    )
    await session.commit()
    return obs.id


async def _seed_agent(session: AsyncSession, agent_id: str) -> None:
    """Insert a minimal agent row for the executor tests."""
    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code') "
            "ON CONFLICT (agent_id) DO NOTHING"
        ),
        {"agent_id": agent_id},
    )
    await session.commit()


# ════════════════════════════════════════════════════════════════════════════
# 1. Basic retrieval — returns only rejections, parses reason, newest first
# ════════════════════════════════════════════════════════════════════════════


async def test_fetch_returns_rejections_only_newest_first(db_session: AsyncSession) -> None:
    """Seed 1 rejection + 1 approval; helper returns only the rejection, reason parsed."""
    from artemis.pipelines.node_executors.agent_memory_context import fetch_agent_rejection_context

    agent_id = "marketing.qualifier.cross_reference"

    # Rejection with reason
    await _seed_observation(
        db_session,
        agent_id=agent_id,
        category="signal_gate1_decision",
        content="Operator rejected signal #1 at Gate 1 on 2026-06-01T00:00:00+00:00. Reason: off-territory. Citations: runs (none).",
    )
    # Approval — must NOT be returned
    await _seed_observation(
        db_session,
        agent_id=agent_id,
        category="signal_gate1_decision",
        content="Operator approved signal #2 at Gate 1 on 2026-06-01T01:00:00+00:00.",
    )
    # Second rejection with different reason
    await _seed_observation(
        db_session,
        agent_id=agent_id,
        category="signal_gate1_decision",
        content="Operator rejected signal #3 at Gate 1 on 2026-06-01T02:00:00+00:00. Reason: weak evidence. Citations: runs (none).",
    )

    results = await fetch_agent_rejection_context(db_session, agent_id)

    assert len(results) == 2, f"Expected 2 rejections, got {len(results)}: {results}"

    # Both should have reason parsed
    reasons = {r["reason"] for r in results}
    assert "off-territory" in reasons, f"Reason 'off-territory' missing from {reasons}"
    assert "weak evidence" in reasons, f"Reason 'weak evidence' missing from {reasons}"

    # Confirm no approval in results
    for entry in results:
        assert " rejected " in entry["content"], (
            f"Unexpected non-rejection entry: {entry['content']}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 2. Limit honored
# ════════════════════════════════════════════════════════════════════════════


async def test_fetch_limit_honored(db_session: AsyncSession) -> None:
    """Seed 10 rejections; limit=3 returns exactly 3."""
    from artemis.pipelines.node_executors.agent_memory_context import fetch_agent_rejection_context

    agent_id = "marketing.qualifier.cross_reference"

    for i in range(10):
        await _seed_observation(
            db_session,
            agent_id=agent_id,
            category="signal_gate1_decision",
            content=f"Operator rejected signal #{i} at Gate 1 on 2026-06-01T00:0{i}:00+00:00.",
        )

    results = await fetch_agent_rejection_context(db_session, agent_id, limit=3)

    assert len(results) == 3, f"Expected 3, got {len(results)}"


# ════════════════════════════════════════════════════════════════════════════
# 3. Wrong agent — another agent's rejections not returned
# ════════════════════════════════════════════════════════════════════════════


async def test_fetch_wrong_agent_returns_empty(db_session: AsyncSession) -> None:
    """Seed rejections for qualifier; query for content adapter → returns []."""
    from artemis.pipelines.node_executors.agent_memory_context import fetch_agent_rejection_context

    qualifier_agent = "marketing.qualifier.cross_reference"
    content_agent = "marketing.content.writing_studio_adapter"

    await _seed_observation(
        db_session,
        agent_id=qualifier_agent,
        category="signal_gate1_decision",
        content="Operator rejected signal #10 at Gate 1 on 2026-06-01T00:00:00+00:00.",
    )

    results = await fetch_agent_rejection_context(db_session, content_agent)

    assert results == [], f"Expected [] for {content_agent}, got {results}"


# ════════════════════════════════════════════════════════════════════════════
# 4. No observations — returns [] without error
# ════════════════════════════════════════════════════════════════════════════


async def test_fetch_no_observations_returns_empty(db_session: AsyncSession) -> None:
    """Agent with zero observations in DB → helper returns []."""
    from artemis.pipelines.node_executors.agent_memory_context import fetch_agent_rejection_context

    results = await fetch_agent_rejection_context(db_session, "marketing.qualifier.cross_reference")

    assert results == []


# ════════════════════════════════════════════════════════════════════════════
# 5. Failure isolation — store raises; helper returns [] + logs warning
# ════════════════════════════════════════════════════════════════════════════


async def test_fetch_failure_isolation_returns_empty_and_logs(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Monkeypatched store raises RuntimeError; fetch returns [] and logs WARNING."""
    from artemis.pipelines.node_executors.agent_memory_context import fetch_agent_rejection_context

    # The helper imports list_observations_for_scope lazily from artemis.memory.store,
    # so we must patch at the canonical import location.
    with (
        patch(
            "artemis.memory.store.list_observations_for_scope",
            new=AsyncMock(side_effect=RuntimeError("simulated store failure")),
        ),
        caplog.at_level(
            logging.WARNING, logger="artemis.pipelines.node_executors.agent_memory_context"
        ),
    ):
        results = await fetch_agent_rejection_context(
            db_session, "marketing.qualifier.cross_reference"
        )

    assert results == []
    assert any("fetch_agent_rejection_context failed" in r.message for r in caplog.records), (
        f"Expected warning log; got records: {[r.message for r in caplog.records]}"
    )


# ════════════════════════════════════════════════════════════════════════════
# 6. executor integration — qualifier agent with 1 rejection gets context injected
# ════════════════════════════════════════════════════════════════════════════


async def test_executor_injects_prior_rejections_for_qualifier_agent(
    db_session: AsyncSession,
) -> None:
    """Qualifier agent with 1 prior rejection → shared_context has prior_rejections
    and prior_rejections_instruction when run_agent is called."""
    from artemis.pipelines.node_executors.agent_executor import execute_agent_node

    agent_id = "marketing.qualifier.cross_reference"

    # Seed the agent row and one rejection observation
    await _seed_agent(db_session, agent_id)
    await _seed_observation(
        db_session,
        agent_id=agent_id,
        category="signal_gate1_decision",
        content=(
            "Operator rejected signal #5 at Gate 1 on 2026-06-01T00:00:00+00:00."
            " Reason: off-territory. Citations: runs (none)."
        ),
    )

    # Create a minimal pipeline run so run_id can be resolved
    await db_session.execute(
        text(
            "INSERT INTO pipelines (id, name, description, nodes, edges, status) "
            "VALUES ('pipe-c3-test', 'C3 Test', '', '[]'::jsonb, '[]'::jsonb, 'active') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO pipeline_runs (id, pipeline_id, status, trigger, triggered_by) "
            "VALUES ('run-c3-test', 'pipe-c3-test', 'running', 'manual', 'test') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await db_session.commit()

    node: dict[str, Any] = {
        "id": "agent_node_1",
        "type": "agent_invocation",
        "label": "qualifier",
        "config": {"agent_id": agent_id},
        "position": {"x": 0.0, "y": 0.0},
    }

    # Capture the shared_context that reaches run_agent
    captured_shared_context: dict[str, Any] | None = None

    async def _fake_run_agent(
        *,
        session: Any,
        agent_id: Any,
        shared_context: Any = None,
        model_adapter: Any = None,
        user_message: Any = None,
        owner_user_id: Any = None,
    ) -> Any:
        nonlocal captured_shared_context
        captured_shared_context = dict(shared_context) if shared_context else {}
        # Return a minimal succeeded AgentRun-like object
        result = AsyncMock()
        result.status = "completed"
        result.error = None
        result.cost_input_tokens = 10
        result.cost_output_tokens = 5
        result.run_id = "fake-run-id-c3"
        return result

    # run_agent and get_agent_context are lazily imported inside execute_agent_node,
    # so patch at the canonical module where they are defined.
    with (
        patch(
            "artemis.builders.executor.run_agent",
            new=_fake_run_agent,
        ),
        patch(
            "artemis.builders.repository.get_agent_context",
            new=AsyncMock(side_effect=ValueError("no context")),
        ),
    ):
        result = await execute_agent_node(
            node=node,
            node_states={},
            session=db_session,
            run_id="run-c3-test",
        )

    assert result["status"] == "succeeded", f"Unexpected status: {result}"
    assert captured_shared_context is not None, "shared_context was never captured"
    assert "prior_rejections" in captured_shared_context, (
        f"prior_rejections missing from shared_context keys: {list(captured_shared_context.keys())}"
    )
    assert "prior_rejections_instruction" in captured_shared_context, (
        "prior_rejections_instruction missing from shared_context"
    )
    rejections = captured_shared_context["prior_rejections"]
    assert isinstance(rejections, list) and len(rejections) == 1, (
        f"Expected 1 rejection entry, got: {rejections}"
    )
    assert rejections[0]["reason"] == "off-territory", f"Reason mismatch: {rejections[0]}"


# ════════════════════════════════════════════════════════════════════════════
# 7. Non-target agent (scout) — keys NOT injected
# ════════════════════════════════════════════════════════════════════════════


async def test_executor_skips_injection_for_scout_agent(db_session: AsyncSession) -> None:
    """Scout agent does NOT get prior_rejections / prior_rejections_instruction."""
    from artemis.pipelines.node_executors.agent_executor import execute_agent_node

    agent_id = "marketing.scout.starbridge_researcher"
    await _seed_agent(db_session, agent_id)

    # Seed a rejection (still attached to the scout's agent scope for realism)
    await _seed_observation(
        db_session,
        agent_id=agent_id,
        category="signal_gate1_decision",
        content="Operator rejected signal #99 at Gate 1 on 2026-06-01T00:00:00+00:00.",
    )

    await db_session.execute(
        text(
            "INSERT INTO pipelines (id, name, description, nodes, edges, status) "
            "VALUES ('pipe-c3-scout', 'C3 Scout', '', '[]'::jsonb, '[]'::jsonb, 'active') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO pipeline_runs (id, pipeline_id, status, trigger, triggered_by) "
            "VALUES ('run-c3-scout', 'pipe-c3-scout', 'running', 'manual', 'test') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await db_session.commit()

    node: dict[str, Any] = {
        "id": "scout_node",
        "type": "agent_invocation",
        "label": "scout",
        "config": {"agent_id": agent_id},
        "position": {"x": 0.0, "y": 0.0},
    }

    captured_shared_context: dict[str, Any] | None = None

    async def _fake_run_agent(
        *,
        session: Any,
        agent_id: Any,
        shared_context: Any = None,
        model_adapter: Any = None,
        user_message: Any = None,
        owner_user_id: Any = None,
    ) -> Any:
        nonlocal captured_shared_context
        captured_shared_context = dict(shared_context) if shared_context else {}
        result = AsyncMock()
        result.status = "completed"
        result.error = None
        result.cost_input_tokens = 10
        result.cost_output_tokens = 5
        result.run_id = "fake-run-scout"
        return result

    # run_agent is lazily imported from artemis.builders.executor inside the function.
    with (
        patch(
            "artemis.builders.executor.run_agent",
            new=_fake_run_agent,
        ),
        patch(
            "artemis.builders.repository.get_agent_context",
            new=AsyncMock(side_effect=ValueError("no context")),
        ),
    ):
        result = await execute_agent_node(
            node=node,
            node_states={},
            session=db_session,
            run_id="run-c3-scout",
        )

    assert result["status"] == "succeeded", f"Unexpected status: {result}"
    assert captured_shared_context is not None
    assert "prior_rejections" not in captured_shared_context, (
        f"prior_rejections should NOT be in scout shared_context; got: {list(captured_shared_context.keys())}"
    )
    assert "prior_rejections_instruction" not in captured_shared_context, (
        "prior_rejections_instruction should NOT be in scout shared_context"
    )


# ════════════════════════════════════════════════════════════════════════════
# 8. Empty list — keys NOT injected
# ════════════════════════════════════════════════════════════════════════════


async def test_executor_skips_injection_when_no_rejections(db_session: AsyncSession) -> None:
    """Qualifier agent with zero prior rejections → keys absent from shared_context."""
    from artemis.pipelines.node_executors.agent_executor import execute_agent_node

    agent_id = "marketing.qualifier.cross_reference"
    await _seed_agent(db_session, agent_id)
    # No observations seeded — agent has a clean slate

    await db_session.execute(
        text(
            "INSERT INTO pipelines (id, name, description, nodes, edges, status) "
            "VALUES ('pipe-c3-empty', 'C3 Empty', '', '[]'::jsonb, '[]'::jsonb, 'active') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await db_session.execute(
        text(
            "INSERT INTO pipeline_runs (id, pipeline_id, status, trigger, triggered_by) "
            "VALUES ('run-c3-empty', 'pipe-c3-empty', 'running', 'manual', 'test') "
            "ON CONFLICT DO NOTHING"
        )
    )
    await db_session.commit()

    node: dict[str, Any] = {
        "id": "qualifier_node",
        "type": "agent_invocation",
        "label": "qualifier",
        "config": {"agent_id": agent_id},
        "position": {"x": 0.0, "y": 0.0},
    }

    captured_shared_context: dict[str, Any] | None = None

    async def _fake_run_agent(
        *,
        session: Any,
        agent_id: Any,
        shared_context: Any = None,
        model_adapter: Any = None,
        user_message: Any = None,
        owner_user_id: Any = None,
    ) -> Any:
        nonlocal captured_shared_context
        captured_shared_context = dict(shared_context) if shared_context else {}
        result = AsyncMock()
        result.status = "completed"
        result.error = None
        result.cost_input_tokens = 10
        result.cost_output_tokens = 5
        result.run_id = "fake-run-empty"
        return result

    # run_agent is lazily imported from artemis.builders.executor inside the function.
    with (
        patch(
            "artemis.builders.executor.run_agent",
            new=_fake_run_agent,
        ),
        patch(
            "artemis.builders.repository.get_agent_context",
            new=AsyncMock(side_effect=ValueError("no context")),
        ),
    ):
        result = await execute_agent_node(
            node=node,
            node_states={},
            session=db_session,
            run_id="run-c3-empty",
        )

    assert result["status"] == "succeeded", f"Unexpected status: {result}"
    assert captured_shared_context is not None
    assert "prior_rejections" not in captured_shared_context, (
        "prior_rejections should be absent when no rejections exist"
    )
    assert "prior_rejections_instruction" not in captured_shared_context, (
        "prior_rejections_instruction should be absent when no rejections exist"
    )
