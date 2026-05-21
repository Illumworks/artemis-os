"""APScheduler integration for scout auto-runs — M5b.
One job per scout, 4h cadence. Mirrors artemis/meetings/scheduler.py.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from artemis.marketing.scout_runner import DEFAULT_CADENCE_SECONDS, ScoutMode
from artemis.marketing.seeds.marketing_agents import MARKETING_AGENT_SPECS

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None
_SCOUT_AGENT_IDS = [
    s.agent_id for s in MARKETING_AGENT_SPECS if s.agent_id.startswith("marketing.scout.")
]


async def _run_scout_job(agent_id: str) -> None:
    from artemis.db import SessionLocal
    from artemis.marketing.scout_runner import run_scout

    async with SessionLocal() as session:
        try:
            result = await run_scout(session, agent_id, ScoutMode.scheduled)
            await session.commit()
            logger.info(
                "scout %s: emitted=%d cost=$%.4f", agent_id, result.signals_emitted, result.cost_usd
            )
        except Exception:
            await session.rollback()
            logger.exception("scout %s: scheduled run failed", agent_id)


def get_scout_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_scout_scheduler() -> None:
    """Start scheduler (idempotent — replace_existing=True)."""
    scheduler = get_scout_scheduler()
    for agent_id in _SCOUT_AGENT_IDS:
        scheduler.add_job(
            _run_scout_job,
            args=[agent_id],
            trigger=IntervalTrigger(seconds=DEFAULT_CADENCE_SECONDS),
            id=f"scout_{agent_id.split('.')[-1]}",
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=300,
        )
    if not scheduler.running:
        scheduler.start()
        logger.info("Scout scheduler started — %d jobs", len(_SCOUT_AGENT_IDS))


def stop_scout_scheduler() -> None:
    """Stop scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None
