"""Headless automation dispatch (OP1).

Reuses the existing execution functions from artemis/builders/executor.py,
workflow_executor.py, chain_executor.py, dag_executor.py.

The automation_run's target_run_id is populated with the resulting underlying
run's id so clients can drill from automation_run → target_run for lineage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.automations import repository as repo
from artemis.automations.models import Automation

logger = logging.getLogger(__name__)


async def dispatch_automation_run(
    session: AsyncSession,
    auto: Automation,
    run_id: str,
) -> None:
    """Dispatch an automation run to its target and update the run record.

    Updates run status to running → succeeded|failed, and populates target_run_id.
    """
    await repo.update_automation_run(session, run_id, status="running")
    await session.flush()

    target_run_id: str | None = None
    error_message: str | None = None
    final_status = "succeeded"

    try:
        target_run_id = await _dispatch_target(session, auto)
    except Exception as exc:
        logger.exception(
            "Automation dispatch target failed: automation=%s run=%s target_type=%s",
            auto.id,
            run_id,
            auto.target_type,
        )
        final_status = "failed"
        error_message = str(exc)

    await repo.update_automation_run(
        session,
        run_id,
        status=final_status,
        completed_at=datetime.now(UTC),
        target_run_id=target_run_id,
        error_message=error_message,
    )


async def _dispatch_target(session: AsyncSession, auto: Automation) -> str | None:
    """Dispatch to the target and return the target_run_id."""
    target_type: str = auto.target_type
    target_id: str = auto.target_id

    if target_type == "agent":
        from artemis.builders.executor import run_agent

        agent_run = await run_agent(
            session=session,
            agent_id=target_id,
            user_message=None,
            shared_context=None,
        )
        run_id_val: Any = agent_run.run_id
        return str(run_id_val) if run_id_val is not None else None

    if target_type == "workflow":
        from artemis.builders.workflow_executor import run_workflow

        wf_run = await run_workflow(
            session=session,
            workflow_id=target_id,
            initial_message=None,
        )
        wf_run_id: Any = wf_run.run_id
        return str(wf_run_id) if wf_run_id is not None else None

    if target_type == "chain":
        from artemis.builders.chain_executor import run_chain

        chain_runs = await run_chain(
            session=session,
            chain_id=target_id,
            initial_message=None,
        )
        if chain_runs:
            first_run_id: Any = chain_runs[0].run_id
            return str(first_run_id) if first_run_id is not None else None
        return None

    if target_type == "dag":
        from artemis.builders.dag_executor import run_dag_with_context

        dag_results = await run_dag_with_context(
            session=session,
            dag_id=target_id,
            initial_inputs=None,
        )
        if dag_results:
            first_dag_run = next(iter(dag_results.values()))
            dag_run_id: Any = first_dag_run.run_id
            return str(dag_run_id) if dag_run_id is not None else None
        return None

    raise ValueError(f"Unknown target_type: {target_type!r}")
