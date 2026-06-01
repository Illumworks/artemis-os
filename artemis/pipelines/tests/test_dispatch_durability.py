"""CC7 + #103 — Dispatch durability + subprocess isolation tests.

Tests:
1. _dispatch_execution retains the reaper task in _BACKGROUND_TASKS while
   the subprocess is running, and discards it on completion (no leak).
2. A dispatched run actually spawns a subprocess (GC footgun regression).
3. Multiple concurrent dispatches all spawn subprocesses.
4. A failing reaper task does not leak in the registry.
5. The spawned subprocess argv targets ``python -m
   artemis.pipelines.run_cli <run_id>`` so the executor never runs in the
   web event loop (#103).
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# Most tests in this file are async; per-test ``@pytest.mark.asyncio`` lets us
# keep two sync tests (run_cli module surface) without the pytestmark warning.


# ── Subprocess stub (no real claude spawn) ────────────────────────────────────


class _FakeProc:
    """Stub for ``asyncio.subprocess.Process`` returned by create_subprocess_exec."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b'{"status": "ok"}\n',
        block_communicate: asyncio.Event | None = None,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._block = block_communicate
        self.pid = 99999

    async def communicate(self) -> tuple[bytes, bytes | None]:
        if self._block is not None:
            await self._block.wait()
        return self._stdout, None

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        if self.returncode is None:
            self.returncode = -9


# ── 1. Registry lifetime: retain while running, discard on completion ─────────


@pytest.mark.asyncio
async def test_background_task_retained_while_running_and_discarded_on_done() -> None:
    """Reaper task lives in _BACKGROUND_TASKS while the subprocess runs; set shrinks back on completion."""
    from artemis.pipelines.routes import _BACKGROUND_TASKS, _dispatch_execution

    initial_count = len(_BACKGROUND_TASKS)

    gate = asyncio.Event()
    proc = _FakeProc(block_communicate=gate)

    async def _fake_create(*_argv: str, **_kwargs: Any) -> _FakeProc:
        return proc

    with patch("asyncio.create_subprocess_exec", new=_fake_create):
        _dispatch_execution("test-run-retain")

        # Give the event loop a chance to start the spawn-and-reap task and
        # hit the blocking ``communicate()``.
        await asyncio.sleep(0)

        assert len(_BACKGROUND_TASKS) == initial_count + 1, (
            "Expected reaper task to be retained in _BACKGROUND_TASKS while subprocess is running"
        )

        # Let the subprocess "finish".
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(_BACKGROUND_TASKS) == initial_count, (
        "Expected reaper task to be discarded from _BACKGROUND_TASKS after subprocess completion"
    )


# ── 2. GC regression: subprocess actually spawns (task not dropped) ───────────


@pytest.mark.asyncio
async def test_dispatch_execution_spawns_subprocess() -> None:
    """create_subprocess_exec is actually called; not silently dropped by GC."""
    from artemis.pipelines.routes import _dispatch_execution

    captured: list[dict[str, Any]] = []

    async def _fake_create(*argv: str, **kwargs: Any) -> _FakeProc:
        captured.append({"argv": list(argv), "kwargs": kwargs})
        return _FakeProc()

    with patch("asyncio.create_subprocess_exec", new=_fake_create):
        _dispatch_execution("gc-regression-run")

        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(captured) == 1, (
        "create_subprocess_exec was not called — reaper task may have been garbage-collected"
    )


# ── 3. argv shape: subprocess targets run_cli, not the in-loop executor ───────


@pytest.mark.asyncio
async def test_dispatch_execution_argv_targets_run_cli_module() -> None:
    """#103: dispatch must spawn ``python -m artemis.pipelines.run_cli <run_id>``."""
    from artemis.pipelines import run_cli
    from artemis.pipelines.routes import _dispatch_execution

    captured: list[dict[str, Any]] = []

    async def _fake_create(*argv: str, **kwargs: Any) -> _FakeProc:
        captured.append({"argv": list(argv), "kwargs": kwargs})
        return _FakeProc()

    # Guard against the executor leaking back into the web loop.
    in_loop_guard = AsyncMock(
        side_effect=AssertionError("PipelineExecutor must not be run in the web loop")
    )

    with (
        patch("asyncio.create_subprocess_exec", new=_fake_create),
        patch("artemis.pipelines.executor.PipelineExecutor.run", new=in_loop_guard),
    ):
        _dispatch_execution("argv-shape-run")
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(captured) == 1
    argv = captured[0]["argv"]
    assert argv[0] == sys.executable
    assert argv[1] == "-m"
    assert argv[2] == run_cli.MODULE_NAME == "artemis.pipelines.run_cli"
    assert argv[3] == "argv-shape-run"
    # cwd must be the repo root so the child's imports resolve.
    assert captured[0]["kwargs"]["cwd"].endswith("artemis-os")
    in_loop_guard.assert_not_awaited()


# ── 4. Multiple concurrent dispatches all spawn ───────────────────────────────


@pytest.mark.asyncio
async def test_multiple_dispatches_all_spawn() -> None:
    """All dispatched runs spawn a subprocess; registry is empty after all complete."""
    from artemis.pipelines.routes import _BACKGROUND_TASKS, _dispatch_execution

    initial_count = len(_BACKGROUND_TASKS)
    n = 5
    captured: list[str] = []

    async def _fake_create(*argv: str, **_kwargs: Any) -> _FakeProc:
        # The run_id is the last positional argv element.
        captured.append(argv[-1])
        return _FakeProc()

    run_ids = [f"multi-run-{i}" for i in range(n)]

    with patch("asyncio.create_subprocess_exec", new=_fake_create):
        for run_id in run_ids:
            _dispatch_execution(run_id)

        # All n reaper tasks should be in the registry before they run.
        assert len(_BACKGROUND_TASKS) >= initial_count + n

        # Let each reaper run.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert sorted(captured) == sorted(run_ids)
    assert len(_BACKGROUND_TASKS) == initial_count


# ── 5. Spawn failure (OSError) is swallowed and reaper is discarded ───────────


@pytest.mark.asyncio
async def test_dispatch_execution_spawn_failure_does_not_leak_task() -> None:
    """If create_subprocess_exec raises OSError, the reaper logs + returns cleanly."""
    from artemis.pipelines.routes import _BACKGROUND_TASKS, _dispatch_execution

    initial_count = len(_BACKGROUND_TASKS)

    async def _boom(*_argv: str, **_kwargs: Any) -> _FakeProc:
        raise OSError("ENOENT: python not found")

    with patch("asyncio.create_subprocess_exec", new=_boom):
        _dispatch_execution("spawn-failure-run")
        # Reaper task should be present until it runs and discovers the OSError.
        assert len(_BACKGROUND_TASKS) == initial_count + 1

        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(_BACKGROUND_TASKS) == initial_count, (
        "Registry leaked after spawn failure"
    )


# ── 6. run_cli module surface (sync) ──────────────────────────────────────────


def test_run_cli_exposes_module_name() -> None:
    """The CLI module is importable for unit tests that don't want to spawn a process."""
    from artemis.pipelines import run_cli

    assert run_cli.MODULE_NAME == "artemis.pipelines.run_cli"
    assert callable(run_cli.main)


def test_run_cli_main_exits_nonzero_without_run_id() -> None:
    """``python -m artemis.pipelines.run_cli`` with no arg → argparse error / non-zero exit."""
    from artemis.pipelines import run_cli

    with pytest.raises(SystemExit) as exc_info:
        run_cli.main([])
    assert exc_info.value.code == 2
