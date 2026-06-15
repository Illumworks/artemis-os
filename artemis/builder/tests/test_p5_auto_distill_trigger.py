"""Tests for P5 semi-autonomous skill-distill trigger (Task B).

Exercises:
1. Crossing N=5 successful runs fires the distiller exactly once.
2. Fewer than N runs does NOT fire the distiller.
3. A trigger error does NOT crash the summarizer.
4. The distiller is not re-fired on every subsequent run (only at 5, 10, 15, …).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.trajectory_summarizer import (
    AgentRunSnapshot,
    _DISTILL_AFTER_N_RUNS,
    _count_runs_since_last_distill,
    _safe_maybe_auto_distill,
    summarize,
)
from artemis.builders.models import (
    Agent,
    AgentRun,
    AgentRunTrajectorySummary,
    DefinitionProposal,
)

# ── helpers ───────────────────────────────────────────────────────────────────


async def _make_agent(session: AsyncSession, agent_id: str | None = None) -> Agent:
    aid = agent_id or f"test-agent-{uuid.uuid4().hex[:8]}"
    agent = Agent(
        agent_id=aid,
        name="Auto-trigger test agent",
        description="",
        system_prompt="You are a test agent.",
        tools=["memory.search"],
        model="claude-sonnet-4-6",
        provider="anthropic",
    )
    session.add(agent)
    await session.flush()
    await session.refresh(agent)
    return agent


async def _make_completed_run(
    session: AsyncSession, agent_id: str, status: str = "completed"
) -> AgentRun:
    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_id=agent_id,
        status=status,
        user_message="Do the thing",
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def _make_summary(session: AsyncSession, run_pk: int) -> AgentRunTrajectorySummary:
    row = AgentRunTrajectorySummary(
        run_id=run_pk,
        what_worked="The agent called memory.search and found relevant context.",
        what_stalled=None,
        what_was_missing=None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


def _fake_summarize_adapter() -> FakeAdapter:
    """Build a minimal FakeAdapter that returns a valid trajectory summary JSON.

    Uses the canonical FakeAdapter (no MagicMock) so cost-event recording
    does not fail with unexpected MagicMock values.  Provides 20 scripted
    replies — enough for any single test.
    """
    raw = json.dumps(
        {"what_worked": "OK", "what_stalled": None, "what_was_missing": None}
    )
    # Supply enough replies for any single test (the summarizer consumes 1-2).
    return FakeAdapter([ScriptedReply(text=raw)] * 20)


def _make_snapshot(agent_id: str, run_pk: int, run_id: str | None = None) -> AgentRunSnapshot:
    return AgentRunSnapshot(
        run_id=run_id or str(uuid.uuid4()),
        run_pk=run_pk,
        agent_id=agent_id,
        status="completed",
        user_message="Do the thing",
        error=None,
    )


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_distill_after_n_runs_constant() -> None:
    """_DISTILL_AFTER_N_RUNS is 5 per the spec."""
    assert _DISTILL_AFTER_N_RUNS == 5


@pytest.mark.asyncio
async def test_count_runs_since_last_distill_no_runs(db_session: AsyncSession) -> None:
    """Count is 0 when there are no runs."""
    async with db_session.begin():
        agent = await _make_agent(db_session)

    async with db_session.begin():
        count = await _count_runs_since_last_distill(db_session, agent.agent_id)

    assert count == 0


@pytest.mark.asyncio
async def test_count_runs_since_last_distill_with_runs(db_session: AsyncSession) -> None:
    """Count returns the number of completed runs with summaries since last distill."""
    async with db_session.begin():
        agent = await _make_agent(db_session)
        for _ in range(3):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    async with db_session.begin():
        count = await _count_runs_since_last_distill(db_session, agent.agent_id)

    assert count == 3


@pytest.mark.asyncio
async def test_count_excludes_failed_runs(db_session: AsyncSession) -> None:
    """Failed runs are not counted even if they have summaries."""
    async with db_session.begin():
        agent = await _make_agent(db_session)
        # 2 completed, 2 failed
        for _ in range(2):
            run = await _make_completed_run(db_session, agent.agent_id, status="completed")
            await _make_summary(db_session, run.id)
        for _ in range(2):
            run = await _make_completed_run(db_session, agent.agent_id, status="failed")
            await _make_summary(db_session, run.id)

    async with db_session.begin():
        count = await _count_runs_since_last_distill(db_session, agent.agent_id)

    assert count == 2


@pytest.mark.asyncio
async def test_count_resets_after_distillation(db_session: AsyncSession) -> None:
    """After a distillation proposal is created, the count resets."""
    import datetime

    async with db_session.begin():
        agent = await _make_agent(db_session)
        # 3 runs before distillation
        for _ in range(3):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    # Simulate a distillation by inserting a self-improvement proposal
    async with db_session.begin():
        proposal = DefinitionProposal(
            kind="skill",
            proposed_by="self-improvement",
            proposed_definition={"slug": "some-skill", "name": "Some skill"},
            citations={"agent_id": agent.agent_id, "run_ids": []},
            status="pending",
        )
        db_session.add(proposal)
        await db_session.flush()

    # Add 2 more runs AFTER the proposal
    async with db_session.begin():
        for _ in range(2):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    async with db_session.begin():
        count = await _count_runs_since_last_distill(db_session, agent.agent_id)

    # Should only count the 2 runs after distillation, not the 3 before
    assert count == 2


@pytest.mark.asyncio
async def test_auto_trigger_fires_at_n5(db_session: AsyncSession) -> None:
    """_safe_maybe_auto_distill fires the distiller exactly once at N=5.

    Tests the count-gate logic directly by calling _safe_maybe_auto_distill
    with 5 completed+summarized runs in the DB.  This avoids relying on
    async task scheduling timing across a NullPool DB connection.
    """
    from artemis.builder.trajectory_summarizer import _safe_maybe_auto_distill

    async with db_session.begin():
        agent = await _make_agent(db_session)
        for _ in range(5):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    distiller_called_with: list[str] = []

    async def _mock_safe_auto_distill(aid: str) -> None:
        distiller_called_with.append(aid)

    with patch(
        "artemis.builder.trajectory_summarizer._safe_auto_distill",
        side_effect=_mock_safe_auto_distill,
    ):
        await _safe_maybe_auto_distill(agent.agent_id, "dummy-run-id")

    assert distiller_called_with == [agent.agent_id], (
        f"Expected distiller to be called once with {agent.agent_id!r}, got {distiller_called_with}"
    )


@pytest.mark.asyncio
async def test_auto_trigger_does_not_fire_before_n5(db_session: AsyncSession) -> None:
    """_safe_maybe_auto_distill does NOT fire when fewer than N runs exist."""
    from artemis.builder.trajectory_summarizer import _safe_maybe_auto_distill

    async with db_session.begin():
        agent = await _make_agent(db_session)
        for _ in range(4):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    distiller_called = False

    async def _mock_safe_auto_distill(aid: str) -> None:
        nonlocal distiller_called
        distiller_called = True

    with patch(
        "artemis.builder.trajectory_summarizer._safe_auto_distill",
        side_effect=_mock_safe_auto_distill,
    ):
        await _safe_maybe_auto_distill(agent.agent_id, "dummy-run-id")

    assert not distiller_called, "Distiller should not fire before N=5 runs"


@pytest.mark.asyncio
async def test_auto_trigger_does_not_re_fire_on_run6(db_session: AsyncSession) -> None:
    """_safe_maybe_auto_distill does NOT fire at run 6 (only at multiples of N)."""
    from artemis.builder.trajectory_summarizer import _safe_maybe_auto_distill

    async with db_session.begin():
        agent = await _make_agent(db_session)
        for _ in range(6):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    distiller_called = False

    async def _mock_safe_auto_distill(aid: str) -> None:
        nonlocal distiller_called
        distiller_called = True

    with patch(
        "artemis.builder.trajectory_summarizer._safe_auto_distill",
        side_effect=_mock_safe_auto_distill,
    ):
        await _safe_maybe_auto_distill(agent.agent_id, "dummy-run-id")

    assert not distiller_called, (
        "Distiller should not re-fire at run 6 (only at multiples of N=5)"
    )


@pytest.mark.asyncio
async def test_auto_trigger_fires_again_at_n10(db_session: AsyncSession) -> None:
    """_safe_maybe_auto_distill fires again at 10 (second multiple of N=5)."""
    from artemis.builder.trajectory_summarizer import _safe_maybe_auto_distill

    async with db_session.begin():
        agent = await _make_agent(db_session)
        for _ in range(10):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)

    distiller_called_with: list[str] = []

    async def _mock_safe_auto_distill(aid: str) -> None:
        distiller_called_with.append(aid)

    with patch(
        "artemis.builder.trajectory_summarizer._safe_auto_distill",
        side_effect=_mock_safe_auto_distill,
    ):
        await _safe_maybe_auto_distill(agent.agent_id, "dummy-run-id")

    assert distiller_called_with == [agent.agent_id], (
        "Distiller should fire at run 10 (second multiple of N=5)"
    )


@pytest.mark.asyncio
async def test_trigger_error_does_not_crash_summarizer(db_session: AsyncSession) -> None:
    """A distiller trigger error must never crash the summarizer."""
    async with db_session.begin():
        agent = await _make_agent(db_session)
        for _ in range(4):
            run = await _make_completed_run(db_session, agent.agent_id)
            await _make_summary(db_session, run.id)
        trigger_run = await _make_completed_run(db_session, agent.agent_id)

    snapshot = _make_snapshot(agent.agent_id, trigger_run.id)
    fake_adapter = _fake_summarize_adapter()

    async def _exploding_maybe_distill(aid: str, run_id: str) -> None:
        raise RuntimeError("Distiller kaboom!")

    with patch(
        "artemis.builder.trajectory_summarizer._safe_maybe_auto_distill",
        side_effect=_exploding_maybe_distill,
    ):
        # Must NOT raise — the summarizer must complete successfully.
        await summarize(snapshot, adapter=fake_adapter, db_session=db_session)
        await asyncio.sleep(0)

    # Verify the trajectory summary was STILL written despite the distiller error.
    # Note: summarize() commits the session internally; use a fresh query in the
    # current auto-begun transaction rather than opening a new explicit begin().
    from sqlalchemy import select as sa_select

    result = await db_session.execute(
        sa_select(AgentRunTrajectorySummary).where(
            AgentRunTrajectorySummary.run_id == trigger_run.id
        )
    )
    row = result.scalar_one_or_none()

    assert row is not None, (
        "Trajectory summary must be written even when the distiller trigger errors"
    )


@pytest.mark.asyncio
async def test_failed_run_does_not_trigger_distiller(db_session: AsyncSession) -> None:
    """Failed runs do not contribute to the trigger count."""
    async with db_session.begin():
        agent = await _make_agent(db_session)
        # 4 completed + the 5th is a failed run (should NOT trigger)
        for _ in range(4):
            run = await _make_completed_run(db_session, agent.agent_id, status="completed")
            await _make_summary(db_session, run.id)
        failed_run = await _make_completed_run(db_session, agent.agent_id, status="failed")

    # snapshot with status="failed" — auto-trigger guard checks snapshot.status
    snapshot = AgentRunSnapshot(
        run_id=str(uuid.uuid4()),
        run_pk=failed_run.id,
        agent_id=agent.agent_id,
        status="failed",
        user_message="Do the thing",
        error="Something went wrong",
    )
    fake_adapter = _fake_summarize_adapter()

    distiller_called = False

    async def _mock_maybe_distill(aid: str, run_id: str) -> None:
        nonlocal distiller_called
        distiller_called = True

    with patch(
        "artemis.builder.trajectory_summarizer._safe_maybe_auto_distill",
        side_effect=_mock_maybe_distill,
    ):
        await summarize(snapshot, adapter=fake_adapter, db_session=db_session)
        await asyncio.sleep(0)

    assert not distiller_called, (
        "Distiller task should not be scheduled for a failed run"
    )
