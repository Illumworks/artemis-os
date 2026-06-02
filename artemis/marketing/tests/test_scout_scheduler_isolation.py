"""#102 — scheduled scouts must execute in a subprocess.

The scheduler used to call ``run_agent`` directly in the web event loop;
its claude-code subprocess leaked semaphores and orphaned a ``claude
worker`` that crashed the FastAPI app. The fix moves cycle execution into
a separate process via ``python -m artemis.marketing.scout_cli``. These
tests prove that property at the scheduler boundary, without spawning a
real claude.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock

import pytest

from artemis.marketing import scout_cli, scout_scheduler


class _FakeProc:
    """Stub for ``asyncio.subprocess.Process`` returned by create_subprocess_exec."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b'{"status": "completed"}\n',
        hang_on_communicate: bool = False,
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self.pid = 99999
        self._hang = hang_on_communicate
        self.killed = False
        self.waited = False

    async def communicate(self) -> tuple[bytes, bytes | None]:
        if self._hang:
            # Wait forever — scheduler's wait_for must time us out + kill.
            await asyncio.sleep(3600)
        return self._stdout, None

    async def wait(self) -> int:
        self.waited = True
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        # Real Process.kill() also lets a pending wait() resolve; simulate
        # by flipping returncode so wait() doesn't block.
        if self.returncode is None:
            self.returncode = -9


@pytest.fixture
def patch_create_subprocess_exec(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every create_subprocess_exec call without spawning a real process."""
    calls: list[dict[str, Any]] = []
    proc_holder: dict[str, _FakeProc] = {}

    async def _fake_create(*argv: str, **kwargs: Any) -> _FakeProc:
        calls.append({"argv": list(argv), "kwargs": kwargs})
        proc = proc_holder.get("proc") or _FakeProc()
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    # Expose proc_holder so individual tests can install a custom proc.
    calls.append({"_proc_holder": proc_holder})  # sentinel at index 0
    return calls


@pytest.mark.asyncio
async def test_run_scout_job_spawns_subprocess_with_correct_argv(
    patch_create_subprocess_exec: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scheduler job spawns the scout_cli module — and never calls run_agent in-loop."""
    run_agent_guard = AsyncMock(
        side_effect=AssertionError("run_agent must not be called in the web loop")
    )
    monkeypatch.setattr("artemis.builders.executor.run_agent", run_agent_guard)

    await scout_scheduler._run_scout_job("marketing.scout.regional_news")

    spawn_calls = [c for c in patch_create_subprocess_exec if "argv" in c]
    assert len(spawn_calls) == 1, "exactly one subprocess spawned per job invocation"
    argv = spawn_calls[0]["argv"]
    assert argv[0] == sys.executable
    assert argv[1] == "-m"
    assert argv[2] == scout_cli.MODULE_NAME == "artemis.marketing.scout_cli"
    assert argv[3] == "marketing.scout.regional_news"
    # cwd must be the repo root so the child's import path is right.
    assert spawn_calls[0]["kwargs"]["cwd"].endswith("artemis-os")
    run_agent_guard.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_scout_job_times_out_kills_proc_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung subprocess is killed + reaped; the scheduler job swallows the timeout."""
    hung_proc = _FakeProc(hang_on_communicate=True, returncode=0)

    async def _fake_create(*_argv: str, **_kwargs: Any) -> _FakeProc:
        return hung_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    # Force the timeout to fire immediately so the test doesn't actually wait 15 min.
    monkeypatch.setattr(scout_scheduler, "SCOUT_SUBPROCESS_TIMEOUT_SECONDS", 0.05)

    # Should return cleanly, not raise.
    await scout_scheduler._run_scout_job("marketing.scout.regional_news")

    assert hung_proc.killed is True
    assert hung_proc.waited is True


@pytest.mark.asyncio
async def test_run_scout_job_nonzero_returncode_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A subprocess that exits non-zero is logged at WARNING; the scheduler keeps running."""
    failed_proc = _FakeProc(returncode=2, stdout=b'{"status": "failed", "error": "boom"}\n')

    async def _fake_create(*_argv: str, **_kwargs: Any) -> _FakeProc:
        return failed_proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    with caplog.at_level("WARNING", logger="artemis.marketing.scout_scheduler"):
        await scout_scheduler._run_scout_job("marketing.scout.regional_news")
    assert any("exit=2" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_run_scout_job_spawn_failure_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If create_subprocess_exec raises (OSError), the job logs + returns cleanly."""

    async def _boom(*_argv: str, **_kwargs: Any) -> _FakeProc:
        raise OSError("ENOENT: python not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)

    # Must not raise — APScheduler should keep the job alive.
    await scout_scheduler._run_scout_job("marketing.scout.regional_news")


def test_scout_cli_main_exits_nonzero_without_agent_id() -> None:
    """``python -m artemis.marketing.scout_cli`` with no arg → argparse error / non-zero exit."""
    with pytest.raises(SystemExit) as exc_info:
        scout_cli.main([])
    # argparse exits with code 2 on missing required positional.
    assert exc_info.value.code == 2


def test_scout_cli_exposes_run_scout_in_db_helper() -> None:
    """The CLI's helper is importable for unit tests that don't want to spawn a process."""
    assert callable(scout_cli._run_scout_in_db)
    assert scout_cli.MODULE_NAME == "artemis.marketing.scout_cli"


def test_scheduler_staggers_first_run_per_job() -> None:
    """start_scout_scheduler gives each job a distinct next_run_time so 9 scouts don't fire together."""
    from datetime import UTC, datetime

    captured: list[dict[str, Any]] = []

    class _StubScheduler:
        running = False

        def add_job(self, *_args: Any, **kwargs: Any) -> None:
            captured.append(kwargs)

        def start(self) -> None:
            type(self).running = True

    stub = _StubScheduler()
    # Patch the module-level scheduler getter.
    import artemis.marketing.scout_scheduler as sched_mod

    original = sched_mod._scheduler
    sched_mod._scheduler = stub
    try:
        sched_mod.start_scout_scheduler()
    finally:
        sched_mod._scheduler = original

    assert len(captured) >= 2  # there are 9 scout agents
    run_times = [j["next_run_time"] for j in captured]
    # Strictly monotonic and spaced — proves staggering is wired.
    assert all(isinstance(t, datetime) and t.tzinfo == UTC for t in run_times)
    deltas = [(run_times[i + 1] - run_times[i]).total_seconds() for i in range(len(run_times) - 1)]
    assert all(d > 0 for d in deltas), "next_run_time must be strictly increasing per job"
