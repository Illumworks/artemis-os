"""Hook registry for agent-loop lifecycle events.

Hooks are observation/instrumentation points; they cannot mutate the message
stream. (Mutation paths go through explicit middleware in later phases.)

Standard event names — keep the literal type below in sync:

  before_request   — about to call the model.
                     payload: CompletionRequest
  after_response   — model returned (before tool execution).
                     payload: CompletionResponse
  before_tool      — about to execute a tool.
                     payload: {"name": str, "input": dict, "tool_use_id": str}
  after_tool       — tool finished (success or error).
                     payload: {"name": str, "input": dict, "tool_use_id": str,
                               "result": str, "is_error": bool, "elapsed_ms": int}
  on_message       — a new message was appended to the conversation.
                     payload: Message
  on_done          — run_turn returned.
                     payload: RunResult
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

logger = logging.getLogger(__name__)

HookEvent = Literal[
    "before_request",
    "after_response",
    "before_tool",
    "after_tool",
    "on_message",
    "on_done",
]

HookCallback = Callable[[Any], Awaitable[None]]


class HookRegistry:
    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[HookCallback]] = {}

    def on(self, event: HookEvent, callback: HookCallback) -> None:
        self._handlers.setdefault(event, []).append(callback)

    async def fire(self, event: HookEvent, payload: Any) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        # Hooks run sequentially. Parallel firing would be faster but makes
        # ordering-sensitive instrumentation (e.g., a logger that paints
        # before_tool / after_tool pairs) harder to reason about.
        for h in handlers:
            try:
                await h(payload)
            except Exception:
                logger.exception("hook %s callback raised; continuing", event)

    def clear(self) -> None:
        self._handlers.clear()


def fire_and_forget(registry: HookRegistry, event: HookEvent, payload: Any) -> asyncio.Task[None]:
    """Schedule a fire without awaiting. Useful when the caller doesn't want
    hook latency to gate the response. Errors in the callback are logged
    (since the task is detached, unhandled exceptions would otherwise be
    silent at GC time)."""
    return asyncio.create_task(registry.fire(event, payload))
