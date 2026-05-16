"""Scripted ModelAdapter for tests — no Anthropic SDK calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artemis.agent.client import CompletionRequest, CompletionResponse, ModelAdapter
from artemis.agent.types import Message, TextBlock, ToolUseBlock, Usage


@dataclass(slots=True)
class ScriptedReply:
    """One pre-scripted response from the fake model.

    Either provide `text` (assistant emits a TextBlock and stops), or
    `tool_calls` (assistant emits ToolUseBlocks and stop_reason=tool_use).
    """

    text: str | None = None
    tool_calls: list[tuple[str, str, dict[str, Any]]] | None = None
    """list of (tool_use_id, name, input)."""
    stop_reason: str = "end_turn"
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0


class FakeAdapter:
    """Returns scripted responses in order. Records every request."""

    def __init__(self, replies: list[ScriptedReply]) -> None:
        self._replies = list(replies)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("FakeAdapter ran out of scripted replies")
        reply = self._replies.pop(0)

        blocks: list[Any] = []
        if reply.text is not None:
            blocks.append(TextBlock(text=reply.text))
        if reply.tool_calls:
            for tid, name, inp in reply.tool_calls:
                blocks.append(ToolUseBlock(id=tid, name=name, input=inp))

        return CompletionResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=reply.stop_reason,
            usage=Usage(
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                cache_read_input_tokens=reply.cache_read_input_tokens,
            ),
        )


# Conform to the ModelAdapter protocol.
_protocol_check: ModelAdapter = FakeAdapter([])
del _protocol_check
