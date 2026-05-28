"""Pipeline execution scheduler — PIPE4.

Manages APScheduler jobs for:
- Scheduled trigger pipelines (cron-based: "fire every 4 hours")
- Gate timeout one-shot jobs (registered by human_gate_executor)
- Sub-pipeline async fire-and-forget jobs (registered by sub_pipeline_executor)

Public API:
  get_pipeline_scheduler() → AsyncIOScheduler
  start_pipeline_scheduler()
  stop_pipeline_scheduler()
  register_pipeline_schedule(pipeline)   — called when a pipeline goes active
  deregister_pipeline_schedule(pipeline) — called when a pipeline is paused/archived

Mirrors the pattern from artemis/automations/scheduler.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[import-untyped]

import artemis.db as _db

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def get_pipeline_scheduler() -> AsyncIOScheduler:
    """Return the singleton pipeline scheduler, creating it if needed."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_pipeline_scheduler() -> None:
    """Load active scheduled pipelines and register cron jobs.

    Idempotent — replace_existing=True prevents duplicate jobs.
    Called from FastAPI lifespan startup.
    Also scans pipeline_runs in running/awaiting_approval for crash-recovery.
    """
    scheduler = get_pipeline_scheduler()

    async def _load_and_register() -> None:
        from sqlalchemy import select

        from artemis.pipelines.models import Pipeline

        async with _db.SessionLocal() as session:
            result = await session.execute(select(Pipeline).where(Pipeline.status == "active"))
            pipelines = list(result.scalars().all())

        count = 0
        for pipeline in pipelines:
            if _has_scheduled_trigger(pipeline):
                _register_pipeline_cron(scheduler, pipeline)
                count += 1

        logger.info("Pipeline scheduler started; registered %d cron jobs", count)

        await sweep_orphaned_queued_runs()

        # Crash recovery: resume any runs that were interrupted mid-flight
        await _recover_interrupted_runs()

    if not scheduler.running:
        scheduler.start()

    asyncio.create_task(_load_and_register())


def stop_pipeline_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Pipeline scheduler stopped")
    _scheduler = None


def register_pipeline_schedule(pipeline: object) -> None:
    """Register or update the cron job for a pipeline.

    Call after pipeline create/update/enable/disable so the scheduler
    reflects the current state without process restart.
    """
    scheduler = get_pipeline_scheduler()
    if not scheduler.running:
        return

    from artemis.pipelines.models import Pipeline as PipelineModel

    if not isinstance(pipeline, PipelineModel):
        return

    job_id = _pipeline_job_id(pipeline.id)

    if pipeline.status in ("paused", "archived"):
        _deregister_job(scheduler, job_id)
    elif pipeline.status == "active" and _has_scheduled_trigger(pipeline):
        _register_pipeline_cron(scheduler, pipeline)
    else:
        _deregister_job(scheduler, job_id)


def deregister_pipeline_schedule(pipeline_id: str) -> None:
    """Remove the cron job for a pipeline."""
    scheduler = get_pipeline_scheduler()
    if not scheduler.running:
        return
    _deregister_job(scheduler, _pipeline_job_id(pipeline_id))


# ── Private helpers ───────────────────────────────────────────────────────────


def _pipeline_job_id(pipeline_id: str) -> str:
    return f"pipeline_cron_{pipeline_id}"


def _has_scheduled_trigger(pipeline: object) -> bool:
    """Return True if the pipeline has a trigger_scheduled node."""
    nodes = getattr(pipeline, "nodes", None) or []
    return any(isinstance(n, dict) and n.get("type") == "trigger_scheduled" for n in nodes)


def _get_cron_config(pipeline: object) -> tuple[str | None, str]:
    """Extract (cron_expr, timezone) from a pipeline's trigger config or nodes."""
    trigger_config = getattr(pipeline, "trigger_config", None) or {}
    cron_expr: str | None = trigger_config.get("cron")
    tz: str = trigger_config.get("timezone", "UTC")

    if not cron_expr:
        nodes = getattr(pipeline, "nodes", None) or []
        for node in nodes:
            if isinstance(node, dict) and node.get("type") == "trigger_scheduled":
                cfg = node.get("config") or {}
                cron_expr = cfg.get("cron")
                tz = cfg.get("timezone", "UTC")
                break

    return cron_expr, tz


def _register_pipeline_cron(scheduler: AsyncIOScheduler, pipeline: object) -> None:
    """Register a cron job for a scheduled-trigger pipeline."""
    pipeline_id: str = getattr(pipeline, "id", "")
    cron_expr, tz = _get_cron_config(pipeline)

    if not cron_expr:
        logger.warning(
            "Pipeline %s is active with trigger_scheduled but has no cron expression; skipping",
            pipeline_id,
        )
        return

    job_id = _pipeline_job_id(pipeline_id)
    try:
        trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
        scheduler.add_job(
            _fire_scheduled_pipeline,
            trigger=trigger,
            id=job_id,
            args=[pipeline_id],
            replace_existing=True,
            max_instances=1,
            misfire_grace_time=120,
        )
        logger.info(
            "Registered cron job for pipeline %s (cron=%r tz=%s)",
            pipeline_id,
            cron_expr,
            tz,
        )
    except Exception:
        logger.exception("Failed to register cron job for pipeline %s", pipeline_id)


def _deregister_job(scheduler: AsyncIOScheduler, job_id: str) -> None:
    """Silently remove a job; no-op if not registered."""
    try:
        scheduler.remove_job(job_id)
        logger.info("Deregistered scheduler job %s", job_id)
    except Exception:
        pass


async def _fire_scheduled_pipeline(pipeline_id: str) -> None:
    """APScheduler callback: create a run and dispatch executor for a scheduled trigger."""
    from artemis.pipelines import repository as repo
    from artemis.pipelines.executor import PipelineExecutor

    async with _db.SessionLocal() as session:
        try:
            pipeline = await repo.get_pipeline(session, pipeline_id)
            if pipeline.status != "active":
                return

            run = await repo.create_pipeline_run(
                session,
                pipeline_id=pipeline_id,
                status="queued",
                trigger="scheduled",
                triggered_by="scheduler",
            )
            await session.commit()

            executor = PipelineExecutor(run.id)
            await executor.run(session)
            await session.commit()
        except Exception:
            logger.exception("Scheduled pipeline run failed: pipeline=%s", pipeline_id)
            await session.rollback()


async def sweep_orphaned_queued_runs(threshold_minutes: int = 5) -> int:
    """Fail queued runs old enough that their executor almost certainly never started.

    TODO (CC7 Part B): Before failing, attempt a single re-dispatch for runs that have
    been queued for 1+ minutes but have empty node_states and have not been re-dispatched
    before (check metadata_['redispatch_count']). Only mark failed if redispatch_count >= 1
    or the run exceeds the full threshold_minutes. Guard strictly on status == 'queued'
    AND empty/absent node_states to avoid double-execution.

    Deferred from CC7 because:
    1. Converting the bulk UPDATE to a per-row loop would exceed the ~60 LOC cap for Part B.
    2. On server restart, _recover_interrupted_runs() already picks up queued+running runs
       immediately, so the sweep's main job is cleanup, not recovery; the GC footgun
       (Part A) was the actual failure mode.
    3. A race exists between _recover_interrupted_runs (startup) and this sweep: both could
       attempt to dispatch the same queued run if the sweeper fires before recovery finishes.
    """
    from sqlalchemy import update

    from artemis.pipelines.models import PipelineRun

    cutoff = datetime.now(UTC) - timedelta(minutes=threshold_minutes)
    async with _db.SessionLocal() as session:
        result = await session.execute(
            update(PipelineRun)
            .where(PipelineRun.status == "queued")
            .where(PipelineRun.created_at < cutoff)
            .values(
                status="failed",
                error_message="Orphaned queued run (executor never started)",
                completed_at=datetime.now(UTC),
            )
            .returning(PipelineRun.id)
        )
        await session.commit()
        swept = len(result.scalars().all())
        if swept:
            logger.warning("Swept %d orphaned queued pipeline run(s)", swept)
        return swept


async def _recover_interrupted_runs() -> None:
    """On startup, resume any pipeline runs that were in-flight when the server stopped."""
    from sqlalchemy import select

    from artemis.pipelines.executor import PipelineExecutor
    from artemis.pipelines.models import PipelineRun

    async with _db.SessionLocal() as session:
        try:
            result = await session.execute(
                select(PipelineRun).where(PipelineRun.status.in_(["running", "queued"]))
            )
            interrupted = list(result.scalars().all())

            if interrupted:
                logger.info(
                    "Recovering %d interrupted pipeline run(s) on startup", len(interrupted)
                )

            for run in interrupted:
                try:
                    executor = PipelineExecutor(run.id)
                    await executor.run(session)
                    await session.commit()
                except Exception:
                    logger.exception("Failed to recover pipeline run %s", run.id)
                    await session.rollback()
        except Exception:
            logger.exception("Failed to load interrupted pipeline runs for recovery")
