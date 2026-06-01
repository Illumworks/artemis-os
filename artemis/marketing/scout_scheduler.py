"""APScheduler integration for scout auto-runs — M5b.
One job per scout, 4h cadence. Mirrors artemis/meetings/scheduler.py.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

from artemis.marketing.scout_runner import DEFAULT_CADENCE_SECONDS
from artemis.marketing.seeds.marketing_agents import MARKETING_AGENT_SPECS

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None
_SCOUT_AGENT_IDS = [
    s.agent_id for s in MARKETING_AGENT_SPECS if s.agent_id.startswith("marketing.scout.")
]


async def _run_scout_job(agent_id: str) -> None:
    # Route scheduled scout runs through the AGENTIC path (run_agent), which uses
    # the agent's real tools (live news fetch + signal_queue.write) and resolves
    # the claude-code subscription adapter from the agent's provider field.
    #
    # The legacy run_scout path fed off stub source adapters (NullAdapter -> []),
    # so every scheduled run emitted zero signals while the scheduler logged
    # "completed" — hollow autonomous production (#101). run_agent is the same
    # path the marketing pipeline already uses to produce real signals.
    from sqlalchemy import select

    from artemis.builders.executor import default_agent_instruction, run_agent
    from artemis.db import SessionLocal
    from artemis.marketing.models import SignalQueue
    from artemis.marketing.repository import create_scout_run, update_scout_run

    slug = agent_id.split(".")[-1]
    async with SessionLocal() as session:
        try:
            run = await run_agent(
                session=session,
                agent_id=agent_id,
                user_message=default_agent_instruction(agent_id),
            )
            signal_ids = (
                (
                    await session.execute(
                        select(SignalQueue.id).where(
                            SignalQueue.provenance["agent_run_id"].astext == run.run_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            # Mirror a scout_runs row so the scout-run history + freshness panels
            # stay accurate now that production flows through run_agent/agent_runs.
            scout_run = await create_scout_run(
                session, run_id=f"sched_{run.run_id}", scout_type=slug, status="pending"
            )
            await update_scout_run(
                session,
                scout_run.id,
                status="complete" if run.status == "completed" else "failed",
                created_signal_ids=[str(sid) for sid in signal_ids],
            )
            await session.commit()
            logger.info(
                "scout %s: run=%s status=%s emitted=%d",
                agent_id,
                run.run_id,
                run.status,
                len(signal_ids),
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
