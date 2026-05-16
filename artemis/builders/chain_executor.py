"""Agent chain executor — sequential composition of agent runs.

Each step passes the prior agent's final_response as the user_message.
The chain is fail-fast by default; steps can override with on_failure='continue'.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.client import ModelAdapter
from artemis.builders.executor import run_agent
from artemis.builders.models import AgentRun
from artemis.builders.repository import (
    get_agent_chain,
    get_agent_context,
)

logger = logging.getLogger(__name__)


async def run_chain(
    *,
    session: AsyncSession,
    chain_id: str,
    initial_message: str | None = None,
    owner_user_id: int | None = None,
    model_adapter: ModelAdapter | None = None,
) -> list[AgentRun]:
    """Execute an agent chain and return the ordered list of AgentRun rows.

    Args:
        session:         SQLAlchemy async session.
        chain_id:        Slug of the chain definition.
        initial_message: Seed message for the first agent step.
        owner_user_id:   User triggering the run.
        model_adapter:   Override adapter (for tests).

    Returns:
        List of AgentRun rows in pipeline order.
    """
    chain = await get_agent_chain(session, chain_id)
    steps: list[dict[str, Any]] = chain.steps if isinstance(chain.steps, list) else []

    runs: list[AgentRun] = []
    current_message: str | None = initial_message

    for i, step in enumerate(steps):
        step_agent_id: str = str(step["agent_id"])
        on_failure: str = str(step.get("on_failure", "fail"))

        try:
            run = await run_agent(
                session=session,
                agent_id=step_agent_id,
                user_message=current_message,
                owner_user_id=owner_user_id,
                model_adapter=model_adapter,
            )
            runs.append(run)

            if run.status == "failed":
                if on_failure == "continue":
                    logger.warning(
                        "Chain '%s' step %d (agent '%s') failed; continuing.",
                        chain_id,
                        i,
                        step_agent_id,
                    )
                    # Carry forward the error message as the next step's input
                    current_message = run.error or ""
                    continue
                else:
                    logger.error(
                        "Chain '%s' step %d (agent '%s') failed; aborting chain.",
                        chain_id,
                        i,
                        step_agent_id,
                    )
                    return runs

            # Feed this step's final_response into the next step
            try:
                ctx = await get_agent_context(session, run.run_id, "final_response")
                current_message = ctx.value if isinstance(ctx.value, str) else str(ctx.value)
            except ValueError:
                current_message = None

        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Chain '%s' step %d (agent '%s') raised unexpectedly", chain_id, i, step_agent_id
            )
            if on_failure != "continue":
                raise
            current_message = f"{type(exc).__name__}: {exc}"

    return runs
