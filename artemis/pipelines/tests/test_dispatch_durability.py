"""CC7 — Dispatch durability tests.

Tests:
1. _dispatch_execution retains the task in _BACKGROUND_TASKS while running,
   and discards it on completion (no leak).
2. A dispatched run actually executes — the task is not garbage-collected
   before the executor runs (GC footgun regression test).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


# ── 1. Registry lifetime: retain while running, discard on completion ─────────


async def test_background_task_retained_while_running_and_discarded_on_done() -> None:
    """Task lives in _BACKGROUND_TASKS during execution; set is empty after completion."""
    from artemis.pipelines.routes import _BACKGROUND_TASKS, _dispatch_execution

    # Start with a clean slate (other tests may have left tasks that already
    # completed and were discarded — that's fine, but we want a predictable
    # starting count to reason about this test's own task).
    initial_count = len(_BACKGROUND_TASKS)

    gate = asyncio.Event()

    async def _blocking_run(run_id: str) -> None:  # noqa: ARG001
        """Stall until the gate opens so we can observe the set while running."""
        await gate.wait()

    with patch(
        "artemis.pipelines.routes._execute_pipeline_run",
        new=_blocking_run,
    ):
        _dispatch_execution("test-run-retain")

        # Task should be present in the registry while it's still running
        assert len(_BACKGROUND_TASKS) == initial_count + 1, (
            "Expected task to be retained in _BACKGROUND_TASKS while running"
        )

        # Allow the task to finish.  Two yields are needed:
        # 1st: event-loop runs _blocking_run past gate.wait() → coroutine returns
        # 2nd: done-callback (_BACKGROUND_TASKS.discard) fires
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # After completion the set should have shrunk back
    assert len(_BACKGROUND_TASKS) == initial_count, (
        "Expected task to be discarded from _BACKGROUND_TASKS after completion"
    )


# ── 2. GC regression: executor actually runs (task not dropped) ───────────────


async def test_dispatch_execution_runs_executor() -> None:
    """_execute_pipeline_run is actually awaited; not silently dropped by GC."""
    from artemis.pipelines.routes import _dispatch_execution

    executed_run_ids: list[str] = []

    async def _capture_run(run_id: str) -> None:
        executed_run_ids.append(run_id)

    with patch(
        "artemis.pipelines.routes._execute_pipeline_run",
        new=_capture_run,
    ):
        _dispatch_execution("gc-regression-run")

        # Yield control so the event loop can run the task to completion.
        # Two yields are sufficient: one to start the coroutine, one to run
        # the done-callback that removes it from the registry.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert "gc-regression-run" in executed_run_ids, (
        "_execute_pipeline_run was not called — task may have been garbage-collected"
    )


# ── 3. Multiple concurrent dispatches all execute ─────────────────────────────


async def test_multiple_dispatches_all_execute() -> None:
    """All dispatched tasks run to completion; registry is empty after all finish."""
    from artemis.pipelines.routes import _BACKGROUND_TASKS, _dispatch_execution

    initial_count = len(_BACKGROUND_TASKS)
    n = 5
    executed: list[str] = []

    async def _capture_run(run_id: str) -> None:
        executed.append(run_id)

    run_ids = [f"multi-run-{i}" for i in range(n)]

    with patch(
        "artemis.pipelines.routes._execute_pipeline_run",
        new=_capture_run,
    ):
        for run_id in run_ids:
            _dispatch_execution(run_id)

        # All n tasks should be in the registry before any yield
        assert len(_BACKGROUND_TASKS) >= initial_count + n, (
            f"Expected at least {initial_count + n} tasks, got {len(_BACKGROUND_TASKS)}"
        )

        # Let all tasks complete
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert sorted(executed) == sorted(run_ids), (
        f"Not all run_ids were executed. Executed: {executed}"
    )
    assert len(_BACKGROUND_TASKS) == initial_count, (
        "Registry leaked: not all tasks were discarded after completion"
    )


# ── 4. Exception in executor does not leak the task in the registry ───────────


async def test_failing_executor_does_not_leak_task() -> None:
    """A task that raises an exception is still removed from _BACKGROUND_TASKS."""
    from artemis.pipelines.routes import _BACKGROUND_TASKS, _dispatch_execution

    initial_count = len(_BACKGROUND_TASKS)

    async def _crashing_run(run_id: str) -> None:  # noqa: ARG001
        raise RuntimeError("synthetic crash for leak test")

    with patch(
        "artemis.pipelines.routes._execute_pipeline_run",
        new=_crashing_run,
    ):
        _dispatch_execution("crash-run")

        # Task is in registry while pending
        assert len(_BACKGROUND_TASKS) == initial_count + 1

        # Allow the task to run (and crash)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    # Even after an exception, done-callback should have discarded the task
    assert len(_BACKGROUND_TASKS) == initial_count, (
        "Registry leaked after executor raised an exception"
    )
