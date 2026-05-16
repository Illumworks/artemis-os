"""Typed event schemas for the WebSocket relay (Phase E2).

All events share the ``WSEvent`` envelope. Helper builder functions
return ``WSEvent`` instances pre-populated with the correct ``type``
and a typed ``payload`` dict.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

WSEventType = Literal[
    "agent_run.started",
    "agent_run.message",
    "agent_run.tool_started",
    "agent_run.tool_completed",
    "agent_run.iteration_complete",
    "agent_run.completed",
    "agent_run.failed",
    "workflow_run.started",
    "workflow_run.step_completed",
    "workflow_run.completed",
    "workflow_run.failed",
]


class WSEvent(BaseModel):
    type: WSEventType
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for ``ws.send_json``."""
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Agent run event builders
# ---------------------------------------------------------------------------


def agent_started_event(run_id: str, agent_id: str, user_message: str) -> WSEvent:
    return WSEvent(
        type="agent_run.started",
        run_id=run_id,
        payload={"agent_id": agent_id, "user_message": user_message},
    )


def agent_message_event(run_id: str, role: str, content: list[Any]) -> WSEvent:
    """A new message was appended to the conversation (assistant or tool-results)."""
    return WSEvent(
        type="agent_run.message",
        run_id=run_id,
        payload={"role": role, "content": content},
    )


def tool_started_event(run_id: str, name: str, input: dict[str, Any], tool_use_id: str) -> WSEvent:
    return WSEvent(
        type="agent_run.tool_started",
        run_id=run_id,
        payload={"name": name, "input": input, "tool_use_id": tool_use_id},
    )


def tool_completed_event(
    run_id: str,
    name: str,
    input: dict[str, Any],
    tool_use_id: str,
    result: str,
    is_error: bool,
    elapsed_ms: int,
) -> WSEvent:
    return WSEvent(
        type="agent_run.tool_completed",
        run_id=run_id,
        payload={
            "name": name,
            "input": input,
            "tool_use_id": tool_use_id,
            "result": result,
            "is_error": is_error,
            "elapsed_ms": elapsed_ms,
        },
    )


def agent_completed_event(
    run_id: str, stop_reason: str, input_tokens: int, output_tokens: int
) -> WSEvent:
    return WSEvent(
        type="agent_run.completed",
        run_id=run_id,
        payload={
            "stop_reason": stop_reason,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    )


def agent_failed_event(run_id: str, error: str) -> WSEvent:
    return WSEvent(
        type="agent_run.failed",
        run_id=run_id,
        payload={"error": error},
    )


# ---------------------------------------------------------------------------
# Workflow run event builders
# ---------------------------------------------------------------------------


def workflow_started_event(run_id: str, workflow_id: str) -> WSEvent:
    return WSEvent(
        type="workflow_run.started",
        run_id=run_id,
        payload={"workflow_id": workflow_id},
    )


def workflow_step_completed_event(
    run_id: str, step_index: int, step_count: int, response_text: str
) -> WSEvent:
    return WSEvent(
        type="workflow_run.step_completed",
        run_id=run_id,
        payload={
            "step_index": step_index,
            "step_count": step_count,
            "response_text": response_text,
        },
    )


def workflow_completed_event(run_id: str, total_cost_usd: float | None) -> WSEvent:
    return WSEvent(
        type="workflow_run.completed",
        run_id=run_id,
        payload={"total_cost_usd": total_cost_usd},
    )


def workflow_failed_event(run_id: str, step_index: int, error: str) -> WSEvent:
    return WSEvent(
        type="workflow_run.failed",
        run_id=run_id,
        payload={"step_index": step_index, "error": error},
    )
