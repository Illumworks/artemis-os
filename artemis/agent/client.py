"""Anthropic API wrapper with prompt caching enabled by default.

Two design choices worth highlighting:

1. **ModelAdapter protocol.** The loop talks to a `ModelAdapter`, not directly
   to the Anthropic SDK. The default `AnthropicAdapter` wraps `AsyncAnthropic`;
   `FakeAdapter` (in tests) substitutes scripted responses without mocking the
   SDK. This keeps the loop testable without `unittest.mock` gymnastics.

2. **Caching by default.** Per the Anthropic-rebuild rule ("prompt caching
   wired from day one") we apply `cache_control` to the last system block and
   the tools list automatically. Callers opt out with `cache_system=False` /
   `cache_tools=False`.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from artemis.agent.types import (
    Message,
    TextBlock,
    Tool,
    ToolUseBlock,
    Usage,
)

if TYPE_CHECKING:
    from artemis.providers.streaming import StreamEvent

# Default model — Anthropic's "latest and most capable" per the system prompt
# guidance. Switch via the `model` argument or `ARTEMIS_AGENT_MODEL` env var.
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(slots=True)
class CompletionRequest:
    messages: list[Message]
    system: str | None = None
    tools: list[Tool] | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    speed_tier: str | None = None
    max_tokens: int = 4096
    cache_system: bool = True
    cache_tools: bool = True


@dataclass(slots=True)
class CompletionResponse:
    """Single non-streaming completion. Multi-block content is preserved."""

    message: Message
    """The assistant message produced by this call."""
    stop_reason: str
    usage: Usage


class ModelAdapter(Protocol):
    """Substitutable boundary between the loop and the LLM provider."""

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...


@runtime_checkable
class SupportsStreaming(Protocol):
    """Optional capability protocol -- adapters that support token streaming.

    Separate from ``ModelAdapter`` so that the loop can probe with
    ``isinstance(adapter, SupportsStreaming)`` rather than duck-typing or
    adding an optional method to the base protocol (which mypy dislikes).

    Adapters that implement this
    ----------------------------
    - ``GeminiAdapter`` -- SSE via ``streamGenerateContent?alt=sse``
    - ``OpenRouterAdapter`` -- OpenAI-format SSE via ``stream: true``

    NOT in scope (separate slice)
    ------------------------------
    - ``AnthropicAdapter`` -- Anthropic SDK has its own streaming helpers
      (``client.messages.stream()``); wiring those is a dedicated future slice
      to keep the streaming surface consistent with SDK idioms.
    """

    async def stream(
        self,
        request: CompletionRequest,
        *,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[StreamEvent]: ...


class AnthropicAdapter:
    """Default adapter — calls the real Anthropic API.

    Reads `ANTHROPIC_API_KEY` from env (via the SDK default). Construction is
    cheap; the underlying httpx client is lazy.

    Streaming is intentionally out of scope for this adapter.  The Anthropic
    SDK exposes ``client.messages.stream()`` with its own async context manager
    idiom; wiring that into ``SupportsStreaming`` is a separate future slice so
    the surface stays consistent with SDK best practices.
    """

    def __init__(self, *, default_model: str | None = None) -> None:
        # Lazy import keeps tests fast and avoids requiring anthropic at
        # import-time if a user only ever needs the FakeAdapter.
        from anthropic import AsyncAnthropic  # noqa: PLC0415

        self._client = AsyncAnthropic()
        self._default_model = default_model or os.environ.get("ARTEMIS_AGENT_MODEL", DEFAULT_MODEL)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        api_messages = [m.to_api() for m in request.messages]

        system_arg = self._build_system(request.system, request.cache_system)
        tools_arg = self._build_tools(request.tools, request.cache_tools)

        kwargs: dict[str, Any] = {
            "model": request.model or self._default_model,
            "max_tokens": request.max_tokens,
            "messages": api_messages,
        }
        if system_arg is not None:
            kwargs["system"] = system_arg
        if tools_arg is not None:
            kwargs["tools"] = tools_arg

        response = await self._client.messages.create(**kwargs)

        blocks: list[Any] = []
        for block in response.content:
            if block.type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                blocks.append(ToolUseBlock(id=block.id, name=block.name, input=dict(block.input)))
            # Other block types (thinking, etc.) ignored for the skeleton —
            # add explicit handling when those features become load-bearing.

        u = response.usage
        usage = Usage(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )

        return CompletionResponse(
            message=Message(role="assistant", content=blocks),
            stop_reason=response.stop_reason or "end_turn",
            usage=usage,
        )

    @staticmethod
    def _build_system(system: str | None, cache: bool) -> list[dict[str, Any]] | None:
        if not system:
            return None
        block: dict[str, Any] = {"type": "text", "text": system}
        if cache:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    @staticmethod
    def _build_tools(tools: list[Tool] | None, cache: bool) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        api_tools = [t.to_api() for t in tools]
        if cache and api_tools:
            # Cache marker goes on the LAST tool. The API caches everything up
            # to (and including) the marker, so this caches the full tools list.
            api_tools[-1] = {**api_tools[-1], "cache_control": {"type": "ephemeral"}}
        return api_tools
