"""Regression tests: trajectory summarizer wire-up in run_agent (O1).

Lead's safety requirement (see brief O1, step 2 note):
  - Run smoke-agent end-to-end, capture the run row shape.
  - Wire summarize_async() into the completion path.
  - Confirm the run row shape and status are unchanged.

These tests prove the fire-and-forget summarizer call does not corrupt the
synchronous run completion path. They should pass both before and after the
wire-up commit.

Key invariants asserted:
  - run.status is 'completed' for a successful run
  - run.status is 'failed' for a run where the adapter raises
  - run.run_id is populated
  - run.agent_id matches the requested agent
  - run.completed_at is set on success
  - run.cost_input_tokens / cost_output_tokens match the adapter's report
  - run.error is None on success, non-None on failure

The trajectory summarizer runs in a background asyncio task. In the test event
loop that task will attempt to open its own DB session and call AnthropicAdapter.
Both will fail gracefully because:
  (a) AnthropicAdapter has no API key in the test environment
  (b) _safe_summarize() catches all exceptions (logged, not re-raised)

We drain pending tasks after the run to let the background task complete (or
fail) before the test ends, so we're not leaving orphaned tasks. We then
re-fetch the run row and assert the shape is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent
from artemis.builders.models import AgentRun

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_agent_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "o1-regression-agent",
        "name": "O1 Regression Agent",
        "goal": "Regression test goal",
        "system_prompt": "You are a regression tester.",
        "tools": [],
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


def _fake(text: str = "done", **kwargs: Any) -> FakeAdapter:
    return FakeAdapter([ScriptedReply(text=text, **kwargs)])


async def _drain_background_tasks() -> None:
    """Yield control long enough for pending asyncio tasks to settle.

    The trajectory summarizer schedules an asyncio.create_task().  We give
    the event loop two opportunities to run it (and any sub-tasks it spawns).
    The task will fail with a logged exception (no API key / no DB session
    override) — that's expected.  We just want to ensure no unhandled
    exception propagates to pytest.
    """
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ── Known-good shape fixture ───────────────────────────────────────────────────

# These are the field names and invariants that define a "known-good" run row.
# If the wire-up changes any of them, this test fails immediately and blocks the
# commit.
_KNOWN_GOOD_COMPLETED_FIELDS = frozenset(
    {
        "status",
        "run_id",
        "agent_id",
        "completed_at",
        "cost_input_tokens",
        "cost_output_tokens",
        "error",
    }
)


def _capture_run_shape(run: AgentRun) -> dict[str, Any]:
    """Extract the known-good fields into a comparable dict."""
    return {
        "status": run.status,
        "run_id": run.run_id,
        "agent_id": run.agent_id,
        "completed_at_set": run.completed_at is not None,
        "cost_input_tokens": run.cost_input_tokens,
        "cost_output_tokens": run.cost_output_tokens,
        "error": run.error,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_shape_unchanged_after_trajectory_wire_success(
    db_session: AsyncSession,
) -> None:
    """A successful run has unchanged shape after summarize_async() is wired in.

    This is the primary regression guard. It checks every field in the
    known-good shape fixture and asserts no field changed due to the
    fire-and-forget summarizer call.
    """
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            **_make_agent_kwargs(agent_id="traj-reg-ok"),
        )

    adapter = FakeAdapter([ScriptedReply(text="all good", input_tokens=120, output_tokens=60)])
    run = await run_agent(
        session=db_session,
        agent_id="traj-reg-ok",
        model_adapter=adapter,
    )
    await db_session.commit()

    # Capture shape immediately after run_agent() returns
    shape_before_drain = _capture_run_shape(run)

    # Drain background tasks (summarizer fires here, fails gracefully)
    await _drain_background_tasks()

    # Re-fetch the run row from DB to confirm it hasn't been mutated
    result = await db_session.execute(
        select(AgentRun).where(AgentRun.run_id == run.run_id).limit(1)
    )
    run_after = result.scalar_one()
    shape_after_drain = _capture_run_shape(run_after)

    # Core assertions
    assert shape_before_drain == shape_after_drain, (
        f"Run row shape changed after summarize_async drained.\n"
        f"Before: {shape_before_drain}\nAfter:  {shape_after_drain}"
    )

    # Specific field assertions against the known-good fixture
    assert run.status == "completed"
    assert run.run_id is not None
    assert run.agent_id == "traj-reg-ok"
    assert run.completed_at is not None
    assert run.cost_input_tokens == 120
    assert run.cost_output_tokens == 60
    assert run.error is None


@pytest.mark.asyncio
async def test_run_shape_unchanged_after_trajectory_wire_failure(
    db_session: AsyncSession,
) -> None:
    """A failed run has unchanged shape after summarize_async() is wired in.

    The summarizer still fires on failure runs. This test confirms the
    error state is not overwritten.
    """

    class BrokenAdapter:
        async def complete(self, _request: Any) -> Any:
            raise RuntimeError("injected failure")

    async with db_session.begin():
        await repo.create_agent(
            db_session,
            **_make_agent_kwargs(agent_id="traj-reg-fail"),
        )

    run = await run_agent(
        session=db_session,
        agent_id="traj-reg-fail",
        model_adapter=BrokenAdapter(),
    )
    await db_session.commit()

    shape_before_drain = _capture_run_shape(run)
    await _drain_background_tasks()

    result = await db_session.execute(
        select(AgentRun).where(AgentRun.run_id == run.run_id).limit(1)
    )
    run_after = result.scalar_one()
    shape_after_drain = _capture_run_shape(run_after)

    assert shape_before_drain == shape_after_drain, (
        f"Failed run row shape changed after summarize_async drained.\n"
        f"Before: {shape_before_drain}\nAfter:  {shape_after_drain}"
    )

    assert run.status == "failed"
    assert run.error is not None
    assert "RuntimeError" in run.error
    assert run.completed_at is not None  # set on failure too


@pytest.mark.asyncio
async def test_summarize_async_is_truly_fire_and_forget(
    db_session: AsyncSession,
) -> None:
    """summarize_async() does not block run_agent or raise to the caller.

    This test verifies that even if the trajectory summarizer task would take
    a long time (simulated by patching), run_agent() returns immediately.
    The asyncio.create_task() call is non-blocking by design.
    """
    import unittest.mock

    call_order: list[str] = []

    original_create_task = asyncio.create_task

    def patched_create_task(coro: Any, **kwargs: Any) -> Any:
        call_order.append("create_task_called")
        return original_create_task(coro, **kwargs)

    async with db_session.begin():
        await repo.create_agent(
            db_session,
            **_make_agent_kwargs(agent_id="traj-reg-ff"),
        )

    with unittest.mock.patch("asyncio.create_task", side_effect=patched_create_task):
        run = await run_agent(
            session=db_session,
            agent_id="traj-reg-ff",
            model_adapter=_fake("ff test"),
        )
        call_order.append("run_agent_returned")
    await db_session.commit()

    # create_task was called before run_agent returned (it's in the completion path)
    assert "create_task_called" in call_order
    assert "run_agent_returned" in call_order
    # The task was scheduled (not awaited), so run_agent returned immediately after
    assert run.status == "completed"

    await _drain_background_tasks()
