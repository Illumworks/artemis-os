"""Agent executor — wires F2a agent definitions to the F1 agent loop.

Public function: run_agent()

This module is the "execution" half of the Builders domain. It loads an Agent
row, creates an AgentRun record, drives run_turn, writes results to
agent_context, and returns the updated AgentRun.

Tool resolution is deliberately stubbed for V1: if agent.tools is non-empty a
warning is logged and the run proceeds with tools=None. Full tool resolution is
a later slice.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent import AnthropicAdapter, run_turn
from artemis.agent.client import ModelAdapter
from artemis.builders.models import AgentRun
from artemis.builders.repository import (
    create_agent_run,
    get_agent,
    set_agent_context,
    set_agent_run_completed,
)

logger = logging.getLogger(__name__)


async def run_agent(
    *,
    session: AsyncSession,
    agent_id: str,
    user_message: str | None = None,
    shared_context: dict[str, object] | None = None,
    owner_user_id: int | None = None,
    model_adapter: ModelAdapter | None = None,
) -> AgentRun:
    """Execute an agent definition and return the completed AgentRun.

    Args:
        session:        SQLAlchemy async session (caller owns commit/rollback).
        agent_id:       Slug of the agent definition to load.
        user_message:   Initial user message. Falls back to agent.goal if None.
        shared_context: Optional key/value dict injected into the system prompt
                        as a ``## Context`` block.
        owner_user_id:  User who triggered this run (for auditing).
        model_adapter:  Override the default AnthropicAdapter — pass a
                        FakeAdapter in tests so no real API calls are made.

    Returns:
        The AgentRun row after completion (status='completed') or failure
        (status='failed').
    """
    # Resolve adapter first — imported name shadows the parameter below
    adapter = model_adapter if model_adapter is not None else AnthropicAdapter()

    # Load agent definition
    agent = await get_agent(session, agent_id)

    # Build system prompt
    system_parts: list[str] = []
    if agent.system_prompt:
        system_parts.append(agent.system_prompt)
    if agent.goal:
        system_parts.append(f"## Goal\n{agent.goal}")
    if shared_context:
        ctx_lines = "\n".join(f"{k}: {v}" for k, v in shared_context.items())
        system_parts.append(f"## Context\n{ctx_lines}")
    system_prompt = "\n\n".join(system_parts) if system_parts else None

    # Tool resolution — V1 stub
    tools_list = agent.tools if isinstance(agent.tools, list) else []
    if tools_list:
        logger.warning(
            "Agent '%s' has tools %r but tool resolution is not yet implemented. "
            "Running with no tools.",
            agent_id,
            tools_list,
        )

    # Choose the user message: explicit > agent.goal > generic
    effective_message: str = user_message or agent.goal or "Please proceed."

    # Create the AgentRun row
    run_id = str(uuid.uuid4())
    run = await create_agent_run(
        session,
        run_id=run_id,
        agent_id=agent_id,
        status="running",
        user_message=effective_message,
        shared_context=shared_context,
        owner_user_id=owner_user_id,
    )
    await session.flush()

    try:
        result = await run_turn(
            adapter=adapter,
            messages=[_user_msg(effective_message)],
            system=system_prompt,
            model=agent.model,
            max_iterations=agent.max_iterations,
            tools=None,
        )

        # Extract final assistant text
        final_text = _extract_text(result)

        # Persist the response into agent_context
        await set_agent_context(session, run_id, "final_response", final_text)

        # Finalise the run row
        run = await set_agent_run_completed(
            session,
            run_id,
            status="completed",
            cost_input_tokens=result.usage.input_tokens,
            cost_output_tokens=result.usage.output_tokens,
        )

    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run '%s' failed", run_id)
        run = await set_agent_run_completed(
            session,
            run_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )

    await session.flush()
    return run


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _user_msg(text: str):  # type: ignore[no-untyped-def]
    from artemis.agent.loop import user_message as _make

    return _make(text)


def _extract_text(result) -> str:  # type: ignore[no-untyped-def]
    """Pull plain text from the last assistant message in *result*."""
    from artemis.agent.types import TextBlock

    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                return " ".join(texts)
    return ""
