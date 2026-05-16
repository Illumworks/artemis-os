"""APScheduler wiring for scout workers.

Uses AsyncIOScheduler (APScheduler 3.x) — one job per enabled scout, running on
IntervalTrigger. max_instances=1 prevents concurrent runs of the same scout if
a previous cycle overruns its interval.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from artemis.scouts.base import BaseScout

_logger = logging.getLogger(__name__)


def create_scheduler(scouts: list[BaseScout]) -> AsyncIOScheduler:
    """Build and return an AsyncIOScheduler with jobs for each enabled scout."""
    scheduler: AsyncIOScheduler = AsyncIOScheduler()
    for scout in scouts:
        if not scout.config.enabled:
            _logger.info("Scout %s disabled — not scheduling.", scout.scout_type)
            continue
        scheduler.add_job(
            scout.run_once,
            trigger=IntervalTrigger(minutes=scout.config.interval_minutes),
            id=scout.scout_type,
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=60,
        )
        _logger.info(
            "Scheduled scout %s every %d min.",
            scout.scout_type,
            scout.config.interval_minutes,
        )
    return scheduler
