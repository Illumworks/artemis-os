"""APScheduler wiring for daily memory maintenance.

Runs category-aware score decay once per day in-process. The scheduled job
opens its own DB session so failures never leak into request transactions.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

import artemis.db as _db
from artemis.memory.maintenance import run_maintenance

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
JOB_ID = "memory_maintenance"
RUN_HOUR_UTC = 3
RUN_MINUTE_UTC = 0


def get_memory_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_memory_scheduler() -> None:
    """Register and start the daily maintenance job."""
    scheduler = get_memory_scheduler()
    scheduler.add_job(
        _run_memory_maintenance_job,
        trigger=CronTrigger(hour=RUN_HOUR_UTC, minute=RUN_MINUTE_UTC, timezone="UTC"),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "Memory maintenance scheduler started (daily at %02d:%02d UTC)",
            RUN_HOUR_UTC,
            RUN_MINUTE_UTC,
        )


def stop_memory_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Memory maintenance scheduler stopped")
    _scheduler = None


async def _run_memory_maintenance_job() -> None:
    """APScheduler callback: decay observation scores in a fresh session."""
    async with _db.SessionLocal() as session:
        try:
            async with session.begin():
                updated = await run_maintenance(session)
            logger.info("Scheduled memory maintenance complete: %s", updated)
        except Exception:
            logger.exception("Scheduled memory maintenance failed")
