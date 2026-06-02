"""APScheduler integration for automation cron jobs (OP1).

Mirrors the pattern from artemis/meetings/scheduler.py and
artemis/integrations/token_refresh/scheduler.py.

On start: reads all active+scheduled automations and registers one cron job
per row using schedule_config.cron + schedule_config.timezone.
On automation create/update/archive: re-register via reregister_automation().
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

import artemis.db as _db
from artemis.automations.models import Automation

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_automation_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_automation_scheduler() -> None:
    """Load all active scheduled automations and register cron jobs.

    Idempotent: calling twice does not duplicate jobs (replace_existing=True).
    Called from FastAPI lifespan startup.
    """
    scheduler = get_automation_scheduler()

    import asyncio

    async def _load_and_register() -> None:
        from sqlalchemy import select

        from artemis.automations.models import Automation as _Automation

        async with _db.SessionLocal() as session:
            result = await session.execute(
                select(_Automation).where(
                    _Automation.status == "active",
                    _Automation.trigger_type == "scheduled",
                )
            )
            automations = list(result.scalars().all())

        for auto in automations:
            _register_job(scheduler, auto)
        logger.info("Automation scheduler started; registered %d cron jobs", len(automations))

    if not scheduler.running:
        scheduler.start()

    asyncio.create_task(_load_and_register())


def stop_automation_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Automation scheduler stopped")
    _scheduler = None


def reregister_automation(auto: Automation) -> None:
    """Update (or remove) the scheduler job for a single automation.

    Called after create, update, or archive so schedule changes take effect
    without restarting the process.
    """
    scheduler = get_automation_scheduler()
    if not scheduler.running:
        return

    job_id = f"automation_{auto.id}"

    if auto.status in ("archived", "paused"):
        _deregister_job(scheduler, job_id)
    elif auto.status == "active" and auto.trigger_type == "scheduled":
        _register_job(scheduler, auto)
    else:
        # manual or webhook: no scheduler job needed
        _deregister_job(scheduler, job_id)


def _register_job(scheduler: AsyncIOScheduler, auto: Automation) -> None:
    """Register or replace a cron job for a scheduled automation."""
    config = auto.schedule_config or {}
    cron_expr = config.get("cron")
    tz = config.get("timezone", "UTC")

    if not cron_expr:
        logger.warning(
            "Automation %s is scheduled but has no schedule_config.cron; skipping",
            auto.id,
        )
        return

    job_id = f"automation_{auto.id}"
    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
        scheduler.add_job(
            _fire_automation,
            trigger=trigger,
            id=job_id,
            args=[auto.id],
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=60,
        )
        logger.info("Registered cron job for automation %s (cron=%r tz=%s)", auto.id, cron_expr, tz)
    except Exception:
        logger.exception("Failed to register cron job for automation %s", auto.id)


def _deregister_job(scheduler: AsyncIOScheduler, job_id: str) -> None:
    try:
        scheduler.remove_job(job_id)
        logger.info("Deregistered scheduler job %s", job_id)
    except Exception:
        pass  # job wasn't registered; that's fine


async def _fire_automation(automation_id: str) -> None:
    """Scheduler callback: create a run record and dispatch."""
    from artemis.automations import repository as repo
    from artemis.automations.dispatch import dispatch_automation_run

    async with _db.SessionLocal() as session:
        try:
            auto = await repo.get_automation(session, automation_id)
            if auto.status != "active":
                return

            run = await repo.create_automation_run(
                session,
                automation_id=automation_id,
                status="queued",
                trigger="scheduled",
                triggered_by="scheduler",
            )
            await session.commit()

            await dispatch_automation_run(session, auto, run.id)
            await session.commit()
        except Exception:
            logger.exception("Scheduled automation run failed: automation=%s", automation_id)
            await session.rollback()
