"""M1 — Tests: trajectory summary → memory observation write path.

Four tests:
1. Integration: create_trajectory_summary also lands a memory_observations row
   + memory_evidence citation back to the run.
2. Idempotency: calling summarize twice with identical content writes exactly
   one observation row (content-hash dedup).
3. Failure isolation: monkeypatching write_observation to raise leaves the
   trajectory summary row intact and does not bubble the exception.
4. Scope creation: with no pre-existing agent scope, the summarizer creates
   exactly one scope row for the agent_id.

Requires ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_os_test.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.builder.repository  # noqa: F401 — register O1 models
import artemis.builders.models  # noqa: F401 — register builder models
import artemis.memory.models  # noqa: F401 — register memory models
import artemis.tools.models  # noqa: F401 — register tool_invocations
from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize
from artemis.builders import repository as builders_repo
from artemis.builders.models import AgentRun, AgentRunTrajectorySummary
from artemis.db import attach_pgvector_codec
from artemis.memory.models import MemoryEvidence, MemoryObservation, MemoryScope

# ── DB URL guard ─────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database. "
        "TRUNCATE would destroy production data. Set ARTEMIS_TEST_DB_URL=...artemis_test."
    )

# ── Truncation SQL — both builder and memory tables ──────────────────────────

_TRUNCATE_SQL = text(
    # Memory tables (child → parent order)
    "TRUNCATE "
    "memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs, "
    # Builder tables
    "tool_invocations, "
    "agent_context, "
    "agent_run_trajectory_summaries, "
    "definition_proposals, "
    "agent_runs, "
    "agent_skills, "
    "workflow_runs, "
    "agents, "
    "skills, "
    "workflows, "
    "agent_chains, "
    "agent_dags, "
    "builder_sessions "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session: truncates both builder and memory tables."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_run(session: AsyncSession, agent_id: str = "m1.test.agent") -> tuple[str, int]:
    """Insert minimal agent + agent_run rows. Returns (run_id str, run.id int)."""
    await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name=f"M1 Test Agent ({agent_id})",
        goal="Test M1 memory observation write",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    run_uuid = str(uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id=agent_id,
        status="completed",
        user_message="Run the marketing pipeline.",
        error=None,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    await session.commit()
    return run_uuid, run.id


def _valid_json() -> str:
    return json.dumps(
        {
            "what_worked": "Scout found 5 signals.",
            "what_stalled": "Qualifier timed out.",
            "what_was_missing": "Rate-limit headroom.",
        }
    )


# ── Test 1: Integration ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trajectory_to_memory_integration(db_session: AsyncSession) -> None:
    """summarize() writes both the trajectory row AND a memory observation + evidence link."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    agent_id = "marketing.qualifier.brief_composer"
    run_uuid, run_pk = await _make_run(db_session, agent_id)

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id=agent_id,
        status="completed",
        user_message="Test integration.",
        error=None,
    )
    adapter = FakeAdapter([ScriptedReply(text=_valid_json())])
    await summarize(snapshot, adapter=adapter, db_session=db_session)

    # (a) trajectory row exists
    traj_result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    traj_row = traj_result.scalar_one_or_none()
    assert traj_row is not None, "No trajectory summary row found"
    assert traj_row.what_worked == "Scout found 5 signals."

    # (b) memory_observations row with correct scope
    obs_result = await db_session.execute(
        select(MemoryObservation).where(
            MemoryObservation.scope_kind == "agent",
            MemoryObservation.scope_id == agent_id,
        )
    )
    obs_row = obs_result.scalar_one_or_none()
    assert obs_row is not None, "No memory_observations row found"
    assert "Scout found 5 signals." in obs_row.content
    assert "Qualifier timed out." in obs_row.content
    assert "Rate-limit headroom." in obs_row.content
    assert obs_row.category == "trajectory"

    # (c) memory_evidence row linking observation to the agent_run
    ev_result = await db_session.execute(
        select(MemoryEvidence).where(
            MemoryEvidence.observation_id == obs_row.id,
            MemoryEvidence.source_kind == "agent_run",
            MemoryEvidence.source_id == run_pk,
        )
    )
    ev_row = ev_result.scalar_one_or_none()
    assert ev_row is not None, "No memory_evidence row found linking obs to agent_run"
    assert ev_row.weight == 1.0


# ── Test 2: Idempotency ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trajectory_to_memory_idempotency(db_session: AsyncSession) -> None:
    """Calling summarize twice for the same run produces exactly one observation row.

    The second call hits the early-return check (trajectory already exists),
    so the memory write path is skipped entirely. The content-hash unique
    constraint on memory_observations is the backstop if the early-return
    were ever bypassed.
    """
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    agent_id = "marketing.scout.federal_funding"
    run_uuid, run_pk = await _make_run(db_session, agent_id)

    summary_json = json.dumps(
        {
            "what_worked": "Identical summary text.",
            "what_stalled": None,
            "what_was_missing": None,
        }
    )
    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id=agent_id,
        status="completed",
        user_message="First call.",
        error=None,
    )

    # First call — writes trajectory row and observation.
    adapter_1 = FakeAdapter([ScriptedReply(text=summary_json)])
    await summarize(snapshot, adapter=adapter_1, db_session=db_session)

    # Second call with the same run — trajectory already exists; returns early.
    # Memory write path is never reached again.
    adapter_2 = FakeAdapter([ScriptedReply(text=summary_json)])
    await summarize(snapshot, adapter=adapter_2, db_session=db_session)

    # Exactly one observation row (second call was a no-op)
    obs_result = await db_session.execute(
        select(MemoryObservation).where(
            MemoryObservation.scope_kind == "agent",
            MemoryObservation.scope_id == agent_id,
        )
    )
    obs_rows = list(obs_result.scalars().all())
    assert len(obs_rows) == 1, f"Expected 1 observation row (idempotent), got {len(obs_rows)}"

    # The second adapter should have received zero LLM calls (early return).
    assert len(adapter_2.requests) == 0, (
        f"Expected 0 LLM calls on second summarize (idempotent), got {len(adapter_2.requests)}"
    )


# ── Test 3: Failure isolation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_memory_write_failure_does_not_break_trajectory(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If write_observation raises, the trajectory row still lands and no exception bubbles."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    agent_id = "marketing.qualifier.signal_writer"
    run_uuid, run_pk = await _make_run(db_session, agent_id)

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id=agent_id,
        status="completed",
        user_message="Test failure isolation.",
        error=None,
    )
    adapter = FakeAdapter([ScriptedReply(text=_valid_json())])

    import artemis.memory.store as _store

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Simulated embedding/DB failure")

    monkeypatch.setattr(_store, "write_observation", _boom)

    with caplog.at_level(logging.WARNING, logger="artemis.builder.trajectory_summarizer"):
        # Must not raise
        await summarize(snapshot, adapter=adapter, db_session=db_session)

    # (a) trajectory row exists
    traj_result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    traj_row = traj_result.scalar_one_or_none()
    assert traj_row is not None, "Trajectory row missing after memory write failure"

    # (b) warning was logged
    warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("M1 memory observation write failed" in m for m in warning_msgs), (
        f"Expected M1 warning. Got: {warning_msgs}"
    )


# ── Test 4: Scope creation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scope_created_on_first_run(db_session: AsyncSession) -> None:
    """With no pre-existing scope, summarize creates exactly one scope row."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    agent_id = "marketing.scout.federal_funding"
    run_uuid, run_pk = await _make_run(db_session, agent_id)

    # Verify scope does not exist yet
    pre_result = await db_session.execute(
        select(MemoryScope).where(
            MemoryScope.scope_kind == "agent",
            MemoryScope.scope_id == agent_id,
        )
    )
    assert pre_result.scalar_one_or_none() is None, "Scope should not exist pre-run"

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id=agent_id,
        status="completed",
        user_message="Test scope creation.",
        error=None,
    )
    adapter = FakeAdapter([ScriptedReply(text=_valid_json())])
    await summarize(snapshot, adapter=adapter, db_session=db_session)

    # Exactly one scope row created
    post_result = await db_session.execute(
        select(MemoryScope).where(
            MemoryScope.scope_kind == "agent",
            MemoryScope.scope_id == agent_id,
        )
    )
    scope_rows = list(post_result.scalars().all())
    assert len(scope_rows) == 1, f"Expected 1 scope row, got {len(scope_rows)}"
    assert scope_rows[0].scope_kind == "agent"
    assert scope_rows[0].scope_id == agent_id
