"""CC10 — GC fix tests for trajectory_summarizer.summarize_async.

Tests confirm the CC7-pattern task-retention fix:

  1. The three-line wiring pattern (create_task + add + add_done_callback)
     keeps the task alive and auto-discards it via the done-callback.
  2. summarize_async() applies this exact pattern: inspecting _BACKGROUND_TASKS
     immediately after the call shows the task is retained.  We verify the
     structural invariant only, without letting the task run (to avoid leaving
     I/O-bound tasks that could deadlock other tests' TRUNCATE statements).
  3. Repository write path: create_trajectory_summary + get_trajectory_summary
     round-trip works correctly (verifies DB plumbing).

Why we don't drain the summarize_async task in tests:
  The task opens a real DB connection (via _db.SessionLocal → asyncpg) and
  then makes an LLM call.  Letting it run during the test suite would leave
  open transactions that deadlock subsequent tests' TRUNCATE statements.
  The task always fails fast in tests (no API key → MissingApiKeyError →
  _safe_summarize catches it), but "fast" is still slow enough to cause
  TRUNCATE races.  We cancel immediately, which sends CancelledError to the
  task at its first await (the asyncpg connection open), preventing any DB
  state.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.builder import trajectory_summarizer as ts
from artemis.builder.repository import create_trajectory_summary, get_trajectory_summary
from artemis.builders import repository as builders_repo
from artemis.builders.models import AgentRun

# ── helpers ───────────────────────────────────────────────────────────────────


async def _commit_agent_run(session: AsyncSession, agent_id: str = "gc-test-agent") -> AgentRun:
    """Insert a minimal agent + agent_run row and return the committed run."""
    await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name="GC Test Agent",
        goal="Test GC fix",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    run = AgentRun(
        run_id=str(uuid.uuid4()),
        agent_id=agent_id,
        status="completed",
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    await session.commit()
    return run


# ── Test 1: the CC7 wiring pattern keeps task alive and auto-discards ─────────


@pytest.mark.asyncio
async def test_background_tasks_set_grows_synchronously(db_session: AsyncSession) -> None:
    """The three-line CC7 pattern (create_task + add + add_done_callback(discard))
    keeps the task alive and auto-discards it after completion.

    We test with a fast local noop so no I/O occurs.  This verifies the wiring
    mechanics in isolation, without risking DB deadlocks from real LLM calls.
    """

    async def _fast_noop() -> None:
        await asyncio.sleep(0)

    size_before = len(ts._BACKGROUND_TASKS)
    task: asyncio.Task[None] = asyncio.create_task(_fast_noop(), name="test_gc_wiring")
    ts._BACKGROUND_TASKS.add(task)
    task.add_done_callback(ts._BACKGROUND_TASKS.discard)
    size_after_schedule = len(ts._BACKGROUND_TASKS)

    # 3 yields: (1) task starts + suspends inside its own sleep, (2) task
    # resumes and completes, (3) done-callbacks fire.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    size_after_drain = len(ts._BACKGROUND_TASKS)

    assert size_after_schedule == size_before + 1, (
        f"Expected _BACKGROUND_TASKS to grow from {size_before} to "
        f"{size_before + 1} immediately after create_task + add, but got "
        f"{size_after_schedule}. The strong-ref add is broken."
    )
    assert size_after_drain == size_before, (
        f"Expected _BACKGROUND_TASKS to shrink back to {size_before} after "
        f"task completion, but got {size_after_drain}. "
        "The done-callback (discard) is broken."
    )


# ── Test 2: summarize_async() applies the CC7 pattern ─────────────────────────


@pytest.mark.asyncio
async def test_summarize_async_applies_cc7_pattern(db_session: AsyncSession) -> None:
    """summarize_async() adds the task to _BACKGROUND_TASKS before yielding.

    We inspect the set immediately after the call (before any event-loop
    iteration) to confirm the strong ref is established synchronously.

    The task is cancelled immediately after the structural check, before it
    can open a DB connection, to prevent TRUNCATE deadlocks in later tests.
    """
    run = await _commit_agent_run(db_session, agent_id="gc-pattern-test")
    size_before = len(ts._BACKGROUND_TASKS)

    await ts.summarize_async(run.id)
    size_after_schedule = len(ts._BACKGROUND_TASKS)

    # Cancel immediately — task hasn't started yet (we haven't yielded).
    # CancelledError fires at the first await inside _safe_summarize, which
    # is the asyncpg connection open — no DB state is touched.
    for t in list(ts._BACKGROUND_TASKS):
        if not t.done():
            t.cancel()
    # Yield to propagate the cancel and fire done-callbacks.
    for _ in range(5):
        await asyncio.sleep(0)

    assert size_after_schedule == size_before + 1, (
        f"summarize_async() should have grown _BACKGROUND_TASKS from "
        f"{size_before} to {size_before + 1} synchronously, but got "
        f"{size_after_schedule}. The CC7-pattern GC fix is not applied."
    )


# ── Test 3: repository write path creates a summary row ───────────────────────


@pytest.mark.asyncio
async def test_summary_row_written_by_repository(db_session: AsyncSession) -> None:
    """create_trajectory_summary() + get_trajectory_summary() round-trip.

    Verifies the DB plumbing is intact. Called directly to isolate the
    repository layer from the LLM adapter.
    """
    run = await _commit_agent_run(db_session, agent_id="gc-repo-test")

    async with db_session.begin():
        await create_trajectory_summary(
            db_session,
            run_id=run.id,
            what_worked="The search tool returned relevant results.",
            what_stalled="The agent looped on the refinement step.",
            what_was_missing="A web-fetch tool was absent.",
        )

    summary = await get_trajectory_summary(db_session, run.id)
    assert summary is not None, f"No trajectory summary row for run_id={run.id}"
    assert summary.run_id == run.id
    assert summary.what_worked is not None
    assert summary.what_stalled is not None
    assert summary.what_was_missing is not None
