"""APScheduler integration for scout auto-runs — M5b.

Stays in-process as the timer. Each cycle's *execution* runs out-of-process:
the job spawns ``python -m artemis.marketing.scout_cli <agent_id>`` and waits
on it. The web process never spawns ``claude`` directly, so a stuck
subprocess / leaked semaphores / orphaned ``claude worker`` can never crash
the FastAPI app (#102). When the CLI exits, the OS reaps its claude
grandchild + every semaphore the subscription adapter created.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from artemis.marketing import scout_cli
from artemis.marketing.scout_runner import DEFAULT_CADENCE_SECONDS
from artemis.marketing.seeds.marketing_agents import MARKETING_AGENT_SPECS

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None
_SCOUT_AGENT_IDS = [
    s.agent_id for s in MARKETING_AGENT_SPECS if s.agent_id.startswith("marketing.scout.")
]

# Per-cycle wall-clock cap. 900s = 15 minutes, well above the largest
# observed CC15 run; on timeout we kill + reap the child.
SCOUT_SUBPROCESS_TIMEOUT_SECONDS = 900

# Stagger the first run of each scheduled job so all 9 scouts don't fire on
# the same tick (which previously hit the claude-code concurrency timeout
# — 2/3 of three concurrent runs failed in past testing). 7 min × 9 jobs =
# the whole fan-out lands inside an hour, well under the 4h cadence.
_STAGGER_MINUTES_PER_JOB = 7

# Repo root — passed as cwd so the subprocess inherits the same project
# layout regardless of where uvicorn was launched from.
_REPO_ROOT = Path(__file__).resolve().parents[2]


async def _run_scout_job(agent_id: str) -> None:
    """Spawn one scout cycle as an isolated subprocess and wait on it.

    Never raises: any subprocess error (non-zero exit, timeout, OSError) is
    logged and swallowed so APScheduler keeps the job alive for the next
    interval.
    """
    argv = [sys.executable, "-m", scout_cli.MODULE_NAME, agent_id]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(_REPO_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError:
        logger.exception("scout %s: failed to spawn subprocess argv=%r", agent_id, argv)
        return

    try:
        stdout, _ = await asyncio.wait_for(
            proc.communicate(), timeout=SCOUT_SUBPROCESS_TIMEOUT_SECONDS
        )
    except TimeoutError:
        # Kill the python child; its claude grandchild gets reaped too.
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        logger.warning(
            "scout %s: subprocess exceeded %ds — killed (pid was %s)",
            agent_id,
            SCOUT_SUBPROCESS_TIMEOUT_SECONDS,
            proc.pid,
        )
        return
    except Exception:
        logger.exception("scout %s: subprocess communicate() failed", agent_id)
        with contextlib.suppress(Exception):
            proc.kill()
            await proc.wait()
        return

    tail = (stdout or b"").decode(errors="replace").strip().splitlines()[-1:] if stdout else []
    last_line = tail[0] if tail else "(no output)"
    if proc.returncode == 0:
        logger.info("scout %s: subprocess exit=0 last_line=%s", agent_id, last_line)
    else:
        logger.warning(
            "scout %s: subprocess exit=%s last_line=%s",
            agent_id,
            proc.returncode,
            last_line,
        )


def get_scout_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_scout_scheduler() -> None:
    """Start scheduler (idempotent — replace_existing=True).

    First-run times are staggered by ``_STAGGER_MINUTES_PER_JOB`` so the 9
    scouts don't all fire on the same tick — a simultaneous fan-out hits
    the claude-code concurrency ceiling and most runs time out.
    """
    scheduler = get_scout_scheduler()
    now = datetime.now(UTC)
    for index, agent_id in enumerate(_SCOUT_AGENT_IDS):
        scheduler.add_job(
            _run_scout_job,
            args=[agent_id],
            trigger=IntervalTrigger(seconds=DEFAULT_CADENCE_SECONDS),
            id=f"scout_{agent_id.split('.')[-1]}",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
            next_run_time=now + timedelta(minutes=index * _STAGGER_MINUTES_PER_JOB),
        )
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "Scout scheduler started — %d jobs, staggered %d min apart",
            len(_SCOUT_AGENT_IDS),
            _STAGGER_MINUTES_PER_JOB,
        )


def stop_scout_scheduler() -> None:
    """Stop scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
