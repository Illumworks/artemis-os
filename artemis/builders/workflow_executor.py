"""Workflow executor — runs sequential workflow steps via the F1 agent loop.

Each step is a discrete run_turn call. Steps can share accumulated context
but V1 does NOT resume Claude sessions between steps (that comes later).
Step responses are stored in agent_context keyed on the workflow_run.id.

See models.WorkflowRun and the 0008_workflow_context migration which adds
workflow_run_id to agent_context.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent import AnthropicAdapter, run_turn
from artemis.agent.client import ModelAdapter
from artemis.builders._cost import estimate_cost_usd
from artemis.builders.models import WorkflowRun
from artemis.builders.repository import (
    create_workflow_run,
    get_workflow,
    set_workflow_context,
    update_workflow_run_status,
)
from artemis.ws.events import (
    workflow_completed_event,
    workflow_failed_event,
    workflow_started_event,
    workflow_step_completed_event,
)
from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)


async def run_workflow(
    *,
    session: AsyncSession,
    workflow_id: str,
    initial_message: str | None = None,
    owner_user_id: int | None = None,
    model_adapter: ModelAdapter | None = None,
) -> WorkflowRun:
    """Execute a sequential workflow and return the completed WorkflowRun.

    Args:
        session:         SQLAlchemy async session.
        workflow_id:     Slug of the workflow definition.
        initial_message: Optional seed message passed to the first step if the
                         step has no explicit prompt override.
        owner_user_id:   User who triggered this run.
        model_adapter:   Override the default AnthropicAdapter (for tests).

    Returns:
        The WorkflowRun row (status='completed' or 'failed').
    """
    adapter = model_adapter if model_adapter is not None else AnthropicAdapter()

    workflow = await get_workflow(session, workflow_id)

    run_id = str(uuid.uuid4())
    wf_run = await create_workflow_run(
        session,
        run_id=run_id,
        workflow_id=workflow_id,
        status="running",
        current_step=0,
        owner_user_id=owner_user_id,
    )
    await session.flush()

    # Broadcast workflow started
    await ws_manager.broadcast(
        run_id,
        workflow_started_event(run_id, workflow_id).to_dict(),
    )

    steps: list[dict[str, Any]] = workflow.steps if isinstance(workflow.steps, list) else []
    total_input_tokens = 0
    total_output_tokens = 0

    for i, step in enumerate(steps):
        prompt: str = step.get("prompt") or initial_message or "Please proceed."
        on_failure: str = step.get("on_failure", "fail")

        try:
            result = await run_turn(
                adapter=adapter,
                messages=[_user_msg(prompt)],
                model="claude-sonnet-4-6",  # Workflows run on default model
            )

            total_input_tokens += result.usage.input_tokens
            total_output_tokens += result.usage.output_tokens

            response_text = _extract_text(result)

            # Store step response keyed by step index, associated to workflow_run
            await set_workflow_context(
                session,
                workflow_run_id=wf_run.id,
                key=f"step_{i}_response",
                value=response_text,
            )

            # Update current_step progress
            wf_run = await update_workflow_run_status(
                session,
                run_id,
                "running",
                current_step=i + 1,
            )

            # Broadcast step completed
            await ws_manager.broadcast(
                run_id,
                workflow_step_completed_event(run_id, i, len(steps), response_text).to_dict(),
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("Workflow '%s' run '%s' failed at step %d", workflow_id, run_id, i)
            if on_failure == "continue":
                # Log but don't stop; store error as the step response
                await set_workflow_context(
                    session,
                    workflow_run_id=wf_run.id,
                    key=f"step_{i}_response",
                    value=f"ERROR: {type(exc).__name__}: {exc}",
                )
                await update_workflow_run_status(
                    session,
                    run_id,
                    "running",
                    current_step=i + 1,
                )
                continue
            else:
                # on_failure='fail' (default)
                cost = estimate_cost_usd(total_input_tokens, total_output_tokens)
                wf_run = await update_workflow_run_status(
                    session,
                    run_id,
                    "failed",
                    completed_at=datetime.now(UTC),
                    total_cost_usd=cost,
                )
                await session.flush()
                error_str = f"{type(exc).__name__}: {exc}"
                await ws_manager.broadcast(
                    run_id,
                    workflow_failed_event(run_id, i, error_str).to_dict(),
                )
                return wf_run

    cost = estimate_cost_usd(total_input_tokens, total_output_tokens)
    wf_run = await update_workflow_run_status(
        session,
        run_id,
        "completed",
        current_step=len(steps),
        completed_at=datetime.now(UTC),
        total_cost_usd=cost,
    )
    await session.flush()
    await ws_manager.broadcast(
        run_id,
        workflow_completed_event(run_id, cost).to_dict(),
    )
    return wf_run


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _user_msg(text: str):  # type: ignore[no-untyped-def]
    from artemis.agent.loop import user_message as _make

    return _make(text)


def _extract_text(result) -> str:  # type: ignore[no-untyped-def]
    from artemis.agent.types import TextBlock

    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                return " ".join(texts)
    return ""
