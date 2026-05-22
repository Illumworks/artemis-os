"""Sub-pipeline node executor.

Handles: sub_pipeline nodes.

Config shape:
  {
    "pipeline_id": str,    # referenced sub-pipeline ID
    "mode":        str     # "inline" | "async_fire_and_forget"
  }

Modes:
  inline:               Instantiate child PipelineExecutor, await its completion.
  async_fire_and_forget: Create the sub_pipeline_run row, dispatch via APScheduler,
                         mark this node complete immediately.

Cycle detection:
  Maintains a call-stack (ancestor_run_ids) to detect direct/transitive cycles.
  Bails with clear error if a pipeline references itself.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def execute_sub_pipeline_node(
    node: dict[str, Any],
    node_states: dict[str, Any],
    session: AsyncSession,
    run_id: str,
    pipeline_id: str,
    ancestor_run_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Execute a sub_pipeline node.

    Args:
        node:              Node dict (id, type, config, label).
        node_states:       Current node_states for this run.
        session:           Async DB session.
        run_id:            Parent pipeline run ID.
        pipeline_id:       Parent pipeline ID (for cycle detection).
        ancestor_run_ids:  Set of ancestor run IDs already in the call stack.

    Returns:
        NodeState-compatible dict.
    """
    from artemis.pipelines import repository as repo
    from artemis.pipelines.executor import PipelineExecutor

    config: dict[str, Any] = node.get("config") or {}
    sub_pipeline_id: str = config.get("pipeline_id", "")
    mode: str = config.get("mode", "inline")

    if not sub_pipeline_id:
        return {
            "status": "failed",
            "error": "sub_pipeline_executor: node config missing 'pipeline_id'",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Cycle detection
    ancestors = ancestor_run_ids or set()
    if sub_pipeline_id == pipeline_id:
        return {
            "status": "failed",
            "error": f"sub_pipeline_executor: cycle detected — pipeline '{sub_pipeline_id}' references itself",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Verify sub-pipeline exists
    try:
        sub_pipeline = await repo.get_pipeline(session, sub_pipeline_id)
    except ValueError:
        return {
            "status": "failed",
            "error": f"sub_pipeline_executor: sub-pipeline '{sub_pipeline_id}' not found",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    if sub_pipeline.status == "archived":
        return {
            "status": "failed",
            "error": f"sub_pipeline_executor: sub-pipeline '{sub_pipeline_id}' is archived",
            "output_summary": "",
            "cost_usd": 0.0,
        }

    # Create the sub-pipeline run row
    sub_run_id = str(uuid.uuid4())
    await repo.create_pipeline_run(
        session,
        id=sub_run_id,
        pipeline_id=sub_pipeline_id,
        status="queued",
        trigger="event",
        triggered_by=f"sub_pipeline:{run_id}:{node.get('id', '')}",
    )
    await session.flush()

    if mode == "async_fire_and_forget":
        # Dispatch via APScheduler and return immediately
        try:
            from artemis.pipelines.scheduler import get_pipeline_scheduler

            scheduler = get_pipeline_scheduler()
            if scheduler.running:
                scheduler.add_job(
                    _run_sub_pipeline,
                    id=f"sub_pipeline_{sub_run_id}",
                    args=[sub_run_id],
                    max_instances=1,
                )
        except Exception:
            logger.exception("Failed to schedule async sub-pipeline run %s", sub_run_id)

        return {
            "status": "succeeded",
            "output_summary": f"Sub-pipeline '{sub_pipeline_id}' dispatched (async, run={sub_run_id[:8]})",
            "sub_run_id": sub_run_id,
            "cost_usd": 0.0,
        }
    else:
        # Inline: await completion
        await session.commit()

        import artemis.db as _db

        async with _db.SessionLocal() as sub_session:
            executor = PipelineExecutor(
                sub_run_id,
                ancestor_run_ids=ancestors | {run_id},
            )
            try:
                await executor.run(sub_session)
                await sub_session.commit()
                sub_run_final = await repo.get_pipeline_run(sub_session, sub_run_id)
                final_status = sub_run_final.status
            except Exception as exc:
                logger.exception("Inline sub-pipeline run %s failed", sub_run_id)
                await sub_session.rollback()
                return {
                    "status": "failed",
                    "error": f"Sub-pipeline '{sub_pipeline_id}' failed: {exc}",
                    "sub_run_id": sub_run_id,
                    "output_summary": "",
                    "cost_usd": 0.0,
                }

        if final_status in ("succeeded", "partial_complete"):
            return {
                "status": "succeeded",
                "output_summary": f"Sub-pipeline '{sub_pipeline_id}' completed (status={final_status})",
                "sub_run_id": sub_run_id,
                "cost_usd": 0.0,
            }
        else:
            return {
                "status": "failed",
                "error": f"Sub-pipeline '{sub_pipeline_id}' ended with status '{final_status}'",
                "sub_run_id": sub_run_id,
                "output_summary": "",
                "cost_usd": 0.0,
            }


async def _run_sub_pipeline(sub_run_id: str) -> None:
    """APScheduler callback for async_fire_and_forget sub-pipeline runs."""
    import artemis.db as _db
    from artemis.pipelines.executor import PipelineExecutor

    async with _db.SessionLocal() as session:
        try:
            executor = PipelineExecutor(sub_run_id)
            await executor.run(session)
            await session.commit()
        except Exception:
            logger.exception("Async sub-pipeline run %s failed", sub_run_id)
            await session.rollback()
