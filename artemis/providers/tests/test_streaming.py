"""Tests for provider SSE streaming methods.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Mocking strategy: use ``httpx.MockTransport`` with a custom handler to avoid
inner classes that fight mypy strict mode. The handler function returns an
``httpx.Response`` with the scripted SSE body, which httpx wraps transparently
so that ``client.stream()`` yields the text correctly.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from artemis.agent.client import CompletionRequest, SupportsStreaming
from artemis.agent.types import Message, TextBlock
from artemis.providers.errors import ProviderAPIError
from artemis.providers.gemini.adapter import GeminiAdapter
from artemis.providers.gemini.models import GEMINI_DEFAULT_MODEL, estimate_cost, resolve_model
from artemis.providers.openrouter.adapter import OpenRouterAdapter
from artemis.providers.streaming import (
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseDelta,
    StreamToolUseStart,
    StreamUsage,
)

pytestmark = pytest.mark.asyncio


# == helpers ===================================================================


def _gemini_adapter() -> GeminiAdapter:
    return GeminiAdapter(api_key="test-gemini-key")


def _openrouter_adapter() -> OpenRouterAdapter:
    return OpenRouterAdapter(api_key="test-or-key")


def _simple_request() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hello")])],
    )


def _sse(*events: dict[str, Any]) -> str:
    """Format JSON dicts as SSE text (double-newline separated)."""
    return "".join(f"data: {json.dumps(ev)}\n\n" for ev in events)


def _or_sse(*events: dict[str, Any], done: bool = True) -> str:
    """OpenRouter SSE with optional [DONE] sentinel."""
    text = _sse(*events)
    if done:
        text += "data: [DONE]\n\n"
    return text


class _FakeStreamTransport(httpx.AsyncBaseTransport):
    """httpx transport that returns a streaming SSE body."""

    def __init__(self, body: str, status: int = 200) -> None:
        self._body = body
        self._status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            self._status,
            content=self._body.encode(),
            headers={"content-type": "text/event-stream"},
        )


def _stream_ctx(*, text: str, status: int = 200) -> Any:
    """Patch ``httpx.AsyncClient`` with a fake streaming transport."""
    transport = _FakeStreamTransport(text, status)

    class _PatchedClient(httpx.AsyncClient):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=transport)

    return patch("httpx.AsyncClient", new=_PatchedClient)


async def _collect(adapter: Any, request: CompletionRequest) -> list[Any]:
    """Drain the stream and return all events as a list."""
    gen = await adapter.stream(request)
    events = []
    async for ev in gen:
        events.append(ev)
    return events


# == Gemini ====================================================================


async def test_gemini_stream_text_deltas() -> None:
    adapter = _gemini_adapter()
    chunks: list[dict[str, Any]] = [
        {"candidates": [{"content": {"parts": [{"text": "Hello"}]}, "finishReason": ""}], "usageMetadata": {}},
        {"candidates": [{"content": {"parts": [{"text": " world"}]}, "finishReason": ""}], "usageMetadata": {}},
        {"candidates": [{"content": {"parts": [{"text": "!"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}},
    ]
    with _stream_ctx(text=_sse(*chunks)):
        events = await _collect(adapter, _simple_request())

    text_events = [e for e in events if isinstance(e, StreamTextDelta)]
    assert len(text_events) == 3
    assert [e.text for e in text_events] == ["Hello", " world", "!"]
    stop = next(e for e in events if isinstance(e, StreamMessageStop))
    assert stop.stop_reason == "end_turn"
    usage = next(e for e in events if isinstance(e, StreamUsage))
    assert usage.input_tokens == 10
    assert usage.output_tokens == 4


async def test_gemini_stream_text_and_function_call() -> None:
    adapter = _gemini_adapter()
    chunks: list[dict[str, Any]] = [
        {"candidates": [{"content": {"parts": [{"text": "Let me search"}]}, "finishReason": ""}], "usageMetadata": {}},
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "search", "args": {"query": "cats"}}}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8}},
    ]
    with _stream_ctx(text=_sse(*chunks)):
        events = await _collect(adapter, _simple_request())

    assert len([e for e in events if isinstance(e, StreamTextDelta)]) == 1
    start_events = [e for e in events if isinstance(e, StreamToolUseStart)]
    delta_events = [e for e in events if isinstance(e, StreamToolUseDelta)]
    assert len(start_events) == 1
    assert start_events[0].name == "search"
    assert len(delta_events) == 1
    assert json.loads(delta_events[0].partial_json) == {"query": "cats"}


async def test_gemini_stream_cancel_stops_early() -> None:
    """Cancel mid-stream; at most one text delta should arrive."""
    adapter = _gemini_adapter()
    cancel = asyncio.Event()

    # We cannot easily inject per-chunk cancellation via MockTransport, so we
    # verify the cancel contract by pre-setting cancel and confirming zero events
    # are yielded after it is set.
    cancel.set()  # already cancelled before we begin

    chunks: list[dict[str, Any]] = [
        {"candidates": [{"content": {"parts": [{"text": "A"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1}},
    ]
    with _stream_ctx(text=_sse(*chunks)):
        gen: AsyncIterator[Any] = await adapter.stream(_simple_request(), cancel=cancel)
        events: list[Any] = []
        async for ev in gen:
            events.append(ev)

    text_events = [e for e in events if isinstance(e, StreamTextDelta)]
    # With cancel pre-set the loop should bail immediately
    assert len(text_events) == 0


async def test_gemini_stream_max_tokens_stop_reason() -> None:
    adapter = _gemini_adapter()
    chunks: list[dict[str, Any]] = [
        {"candidates": [{"content": {"parts": [{"text": "truncated"}]}, "finishReason": "MAX_TOKENS"}], "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 100}},
    ]
    with _stream_ctx(text=_sse(*chunks)):
        events = await _collect(adapter, _simple_request())
    stop = next(e for e in events if isinstance(e, StreamMessageStop))
    assert stop.stop_reason == "max_tokens"


async def test_gemini_stream_cost_computed() -> None:
    adapter = _gemini_adapter()
    model_id = resolve_model(GEMINI_DEFAULT_MODEL)
    chunks: list[dict[str, Any]] = [
        {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 500}},
    ]
    with _stream_ctx(text=_sse(*chunks)):
        events = await _collect(adapter, _simple_request())
    usage = next(e for e in events if isinstance(e, StreamUsage))
    assert usage.cost_usd is not None
    assert abs(usage.cost_usd - estimate_cost(model_id, 1000, 500)) < 1e-9


# == OpenRouter ================================================================


async def test_openrouter_stream_text_deltas() -> None:
    adapter = _openrouter_adapter()
    deltas = ["The ", "quick ", "brown ", "fox"]
    chunks: list[dict[str, Any]] = [{"choices": [{"delta": {"content": d}, "finish_reason": None}]} for d in deltas]
    chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_cost": 0.001}})
    with _stream_ctx(text=_or_sse(*chunks)):
        events = await _collect(adapter, _simple_request())
    text_events = [e for e in events if isinstance(e, StreamTextDelta)]
    assert len(text_events) == 4
    assert [e.text for e in text_events] == deltas
    assert next(e for e in events if isinstance(e, StreamMessageStop)).stop_reason == "end_turn"
    usage = next(e for e in events if isinstance(e, StreamUsage))
    assert usage.cost_usd is not None
    assert abs(usage.cost_usd - 0.001) < 1e-9


async def test_openrouter_stream_tool_call_across_chunks() -> None:
    adapter = _openrouter_adapter()
    chunk1: dict[str, Any] = {"choices": [{"delta": {"tool_calls": [{"id": "call-abc", "type": "function", "function": {"name": "calculator", "arguments": ""}}]}, "finish_reason": None}]}
    chunk2: dict[str, Any] = {"choices": [{"delta": {"tool_calls": [{"id": "call-abc", "type": "function", "function": {"name": "", "arguments": '{"expr": "2+2"}'}}]}, "finish_reason": None}]}
    chunk3: dict[str, Any] = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    with _stream_ctx(text=_or_sse(chunk1, chunk2, chunk3)):
        events = await _collect(adapter, _simple_request())
    start_events = [e for e in events if isinstance(e, StreamToolUseStart)]
    delta_events = [e for e in events if isinstance(e, StreamToolUseDelta)]
    assert len(start_events) == 1
    assert start_events[0].name == "calculator"
    assert "2+2" in "".join(e.partial_json for e in delta_events)
    assert next(e for e in events if isinstance(e, StreamMessageStop)).stop_reason == "tool_use"


async def test_openrouter_stream_done_sentinel_exits_cleanly() -> None:
    adapter = _openrouter_adapter()
    chunk: dict[str, Any] = {"choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 1}}
    with _stream_ctx(text=_or_sse(chunk, done=True)):
        events = await _collect(adapter, _simple_request())
    assert len([e for e in events if isinstance(e, StreamTextDelta)]) == 1
    assert len([e for e in events if isinstance(e, StreamMessageStop)]) == 1
    assert len([e for e in events if isinstance(e, StreamUsage)]) == 1
    assert len(events) == 3


async def test_openrouter_stream_http_error_raises() -> None:
    adapter = _openrouter_adapter()
    with (
        _stream_ctx(text="unauthorized", status=401),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        gen = await adapter.stream(_simple_request())
        async for _ in gen:
            pass
    assert exc_info.value.status_code == 401
    assert "unauthorized" in exc_info.value.body


async def test_openrouter_stream_cancel_stops_early() -> None:
    """Pre-set cancel; no text deltas should arrive."""
    adapter = _openrouter_adapter()
    cancel = asyncio.Event()
    cancel.set()

    chunk: dict[str, Any] = {"choices": [{"delta": {"content": "A"}, "finish_reason": None}]}
    with _stream_ctx(text=_or_sse(chunk)):
        gen: AsyncIterator[Any] = await adapter.stream(_simple_request(), cancel=cancel)
        events: list[Any] = []
        async for ev in gen:
            events.append(ev)
    assert len([e for e in events if isinstance(e, StreamTextDelta)]) == 0


async def test_openrouter_stream_cost_defaults_zero_when_absent() -> None:
    adapter = _openrouter_adapter()
    chunk: dict[str, Any] = {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}
    with _stream_ctx(text=_or_sse(chunk)):
        events = await _collect(adapter, _simple_request())
    assert next(e for e in events if isinstance(e, StreamUsage)).cost_usd == 0.0


async def test_openrouter_stream_finish_reason_length() -> None:
    adapter = _openrouter_adapter()
    chunk: dict[str, Any] = {"choices": [{"delta": {"content": "truncated"}, "finish_reason": "length"}], "usage": {"prompt_tokens": 5, "completion_tokens": 100}}
    with _stream_ctx(text=_or_sse(chunk)):
        events = await _collect(adapter, _simple_request())
    assert next(e for e in events if isinstance(e, StreamMessageStop)).stop_reason == "max_tokens"


async def test_openrouter_stream_429_raises() -> None:
    adapter = _openrouter_adapter()
    with (
        _stream_ctx(text="rate limited", status=429),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        gen = await adapter.stream(_simple_request())
        async for _ in gen:
            pass
    assert exc_info.value.status_code == 429


# == Protocol conformance ======================================================


def test_gemini_adapter_satisfies_supports_streaming() -> None:
    assert isinstance(_gemini_adapter(), SupportsStreaming)


def test_openrouter_adapter_satisfies_supports_streaming() -> None:
    assert isinstance(_openrouter_adapter(), SupportsStreaming)


def test_anthropic_adapter_does_not_satisfy_supports_streaming() -> None:
    from artemis.agent.client import AnthropicAdapter

    assert not hasattr(AnthropicAdapter, "stream") or not callable(
        getattr(AnthropicAdapter, "stream", None)
    )
