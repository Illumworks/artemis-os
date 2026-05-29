"""The agent loop — send messages, execute tool calls, repeat until end_turn.

Design notes:

- `run_turn` is the only public function. Callers pass an initial message list,
  optional tools, and optional system prompt; the loop returns the full
  conversation including the assistant's response and any tool_result blocks
  it generated along the way.
- Tool execution failures don't crash the loop: an exception in a tool impl
  becomes a `ToolResultBlock(is_error=True)` and the conversation continues.
  The model decides whether to retry or abandon.
- `max_iterations` caps total model calls. Default 10 — generous enough for
  reasonable agent runs, tight enough that infinite-loop bugs don't run up
  the bill. Exceeded → `stop_reason="max_iterations"`.

Reference: claudeck-artemis/server/agent-loop.js — the Node implementation
folds memory injection, skill injection, push notifications, and DB-side
recording into the same loop. The Python rebuild keeps the LOOP narrow and
isolates those concerns into hooks (this module) and explicit middleware
(later phases). Don't merge them back in.
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from artemis.agent.client import (
    CompletionRequest,
    CompletionResponse,
    ModelAdapter,
)
from artemis.agent.hooks import HookRegistry
from artemis.agent.tools import ToolRegistry
from artemis.agent.types import (
    Message,
    RunResult,
    StopReason,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)

logger = logging.getLogger(__name__)


async def run_turn(
    *,
    adapter: ModelAdapter,
    messages: list[Message],
    tools: ToolRegistry | None = None,
    system: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    speed_tier: str | None = None,
    max_tokens: int = 4096,
    max_iterations: int = 10,
    hooks: HookRegistry | None = None,
    cache_system: bool = True,
    cache_tools: bool = True,
) -> RunResult:
    """Run a turn to completion (potentially many model calls if tools are used).

    Returns the full conversation including the assistant's new messages and
    any tool_result blocks the loop produced. `messages` is treated as
    append-only; the input list is not mutated.
    """
    conversation: list[Message] = list(messages)
    tool_specs = tools.specs() if tools else None
    total_usage = Usage()
    iterations = 0
    stop_reason: StopReason = "end_turn"

    for i in range(1, max_iterations + 1):
        iterations = i

        request = CompletionRequest(
            messages=conversation,
            system=system,
            tools=tool_specs,
            model=model,
            reasoning_effort=reasoning_effort,
            speed_tier=speed_tier,
            max_tokens=max_tokens,
            cache_system=cache_system,
            cache_tools=cache_tools,
        )

        if hooks:
            await hooks.fire("before_request", request)

        response: CompletionResponse = await adapter.complete(request)
        total_usage.add(response.usage)

        if hooks:
            await hooks.fire("after_response", response)

        conversation.append(response.message)
        if hooks:
            await hooks.fire("on_message", response.message)

        # End conditions other than tool_use stop the loop.
        if response.stop_reason != "tool_use":
            stop_reason = _normalize_stop_reason(response.stop_reason)
            break

        # Find tool_use blocks and execute each. If no tools are registered
        # but the model emitted tool_use, we surface that as errors per block.
        tool_uses = [b for b in response.message.content if isinstance(b, ToolUseBlock)]
        if not tool_uses:
            # Stop reason said tool_use but no blocks found — defensive.
            stop_reason = "end_turn"
            break

        result_blocks: list[ToolResultBlock] = []
        for use in tool_uses:
            block = await _execute_tool(use, tools, hooks)
            result_blocks.append(block)

        # Tool results all go in a single user message, as per the Anthropic
        # tool-use protocol.
        tool_message = Message(role="user", content=list(result_blocks))
        conversation.append(tool_message)
        if hooks:
            await hooks.fire("on_message", tool_message)

    else:
        # for/else: ran out of iterations without hitting break.
        stop_reason = "max_iterations"

    result = RunResult(
        messages=conversation,
        stop_reason=stop_reason,
        usage=total_usage,
        iterations=iterations,
    )
    if hooks:
        await hooks.fire("on_done", result)
    return result


async def _execute_tool(
    use: ToolUseBlock,
    tools: ToolRegistry | None,
    hooks: HookRegistry | None,
) -> ToolResultBlock:
    if tools is None or use.name not in tools:
        return ToolResultBlock(
            tool_use_id=use.id,
            content=f"tool {use.name!r} is not registered",
            is_error=True,
        )

    entry = tools.get(use.name)
    assert entry is not None  # name-in-registry check above

    # H1: validate input against the tool's JSONSchema before execution.
    # Returns a self-teaching error string on failure, None on success.
    validation_error = tools.validate_input(use.name, use.input)
    if validation_error is not None:
        return ToolResultBlock(
            tool_use_id=use.id,
            content=validation_error,
            is_error=True,
        )

    payload = {"name": use.name, "input": use.input, "tool_use_id": use.id}
    if hooks:
        await hooks.fire("before_tool", payload)

    started = time.monotonic()
    is_error = False
    try:
        content = await entry.impl(use.input)
    except Exception as exc:  # noqa: BLE001 — tools are user code; never crash the loop
        logger.exception("tool %s raised", use.name)
        content = f"{type(exc).__name__}: {exc}"
        is_error = True

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if hooks:
        await hooks.fire(
            "after_tool",
            {**payload, "result": content, "is_error": is_error, "elapsed_ms": elapsed_ms},
        )

    return ToolResultBlock(tool_use_id=use.id, content=content, is_error=is_error)


def _normalize_stop_reason(raw: str) -> StopReason:
    if raw in ("end_turn", "max_tokens", "stop_sequence", "tool_use"):
        return raw  # type: ignore[return-value]
    return "end_turn"


# Convenience helpers for callers that don't want to construct Message objects
# from scratch every time.


def user_message(text: str) -> Message:
    return Message(role="user", content=[TextBlock(text=text)])


def assistant_message(text: str) -> Message:
    return Message(role="assistant", content=[TextBlock(text=text)])


__all__ = [
    "assistant_message",
    "run_turn",
    "user_message",
]


# silence "unused import" — `replace` is exported for callers
_ = replace
