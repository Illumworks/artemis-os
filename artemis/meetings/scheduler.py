"""APScheduler wiring for the meeting auto-summarizer (J6d).

The scheduler is in-process (AsyncIOScheduler), started on app startup and
stopped on shutdown. It fires run_summarizer_tick() every 2 minutes.

Cadence rationale: 2-minute interval × 30-minute lookback window means a
meeting that ended moments ago will be picked up within 2 minutes at worst.
See decisions/auto-meeting-summary.md for full rationale.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from artemis.meetings.summarizer import run_summarizer_tick

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

CADENCE_MINUTES = 2


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_meeting_scheduler() -> None:
    """Start the scheduler. Called from FastAPI lifespan startup.

    Safe to call when already running (add_job with replace_existing=True
    handles re-registration; start() is skipped if already running).
    """
    scheduler = get_scheduler()
    scheduler.add_job(
        run_summarizer_tick,
        trigger=IntervalTrigger(minutes=CADENCE_MINUTES),
        id="meeting_summarizer",
        replace_existing=True,
        max_instances=1,  # never overlap ticks
        misfire_grace_time=30,
    )
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "Meeting summarizer scheduler started (cadence=%d min, window=30 min)",
            CADENCE_MINUTES,
        )


def stop_meeting_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Meeting summarizer scheduler stopped")
    _scheduler = None
