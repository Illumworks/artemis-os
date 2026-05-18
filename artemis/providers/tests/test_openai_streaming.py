"""Tests for the OpenAI adapter SSE streaming method.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Mocking strategy: same ``_FakeStreamTransport`` / ``_stream_ctx`` pattern used
in test_streaming.py — fake transport returns pre-scripted SSE body without
hitting the network.
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
from artemis.providers.openai.adapter import OpenAIAdapter
from artemis.providers.openai.models import estimate_openai_cost
from artemis.providers.streaming import (
    StreamMessageStop,
    StreamTextDelta,
    StreamToolUseDelta,
    StreamToolUseStart,
    StreamUsage,
)

pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────


def _adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-openai-key")


def _simple_request(model: str | None = None) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hello")])],
        model=model,
    )


def _sse(*events: dict[str, Any], done: bool = True) -> str:
    """Format JSON dicts as SSE text with optional [DONE] sentinel."""
    text = "".join(f"data: {json.dumps(ev)}\n\n" for ev in events)
    if done:
        text += "data: [DONE]\n\n"
    return text


class _FakeStreamTransport(httpx.AsyncBaseTransport):
    """httpx transport that returns a scripted SSE body."""

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


async def _collect(adapter: OpenAIAdapter, request: CompletionRequest) -> list[Any]:
    """Drain the stream and return all events as a list."""
    gen = await adapter.stream(request)
    events: list[Any] = []
    async for ev in gen:
        events.append(ev)
    return events


# ── 1. text streaming → StreamTextDelta ───────────────────────────────────


async def test_stream_text_deltas() -> None:
    adapter = _adapter()
    deltas = ["The ", "quick ", "brown ", "fox"]
    chunks: list[dict[str, Any]] = [
        {"choices": [{"delta": {"content": d}, "finish_reason": None}]} for d in deltas
    ]
    chunks.append(
        {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 4},
        }
    )

    with _stream_ctx(text=_sse(*chunks)):
        events = await _collect(adapter, _simple_request())

    text_events = [e for e in events if isinstance(e, StreamTextDelta)]
    assert len(text_events) == 4
    assert [e.text for e in text_events] == deltas
    stop = next(e for e in events if isinstance(e, StreamMessageStop))
    assert stop.stop_reason == "end_turn"


# ── 2. tool_call streaming across multiple SSE chunks ─────────────────────


async def test_stream_tool_call_across_chunks() -> None:
    adapter = _adapter()
    chunk1: dict[str, Any] = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call-abc",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": ""},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    chunk2: dict[str, Any] = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "call-abc",
                            "type": "function",
                            "function": {"name": "", "arguments": '{"expr": "2+2"}'},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    chunk3: dict[str, Any] = {
        "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }

    with _stream_ctx(text=_sse(chunk1, chunk2, chunk3)):
        events = await _collect(adapter, _simple_request())

    start_events = [e for e in events if isinstance(e, StreamToolUseStart)]
    delta_events = [e for e in events if isinstance(e, StreamToolUseDelta)]
    assert len(start_events) == 1
    assert start_events[0].name == "calculator"
    assert start_events[0].id == "call-abc"
    assert "2+2" in "".join(e.partial_json for e in delta_events)

    stop = next(e for e in events if isinstance(e, StreamMessageStop))
    assert stop.stop_reason == "tool_use"


# ── 3. [DONE] sentinel exits loop cleanly ─────────────────────────────────


async def test_stream_done_sentinel_exits_cleanly() -> None:
    adapter = _adapter()
    chunk: dict[str, Any] = {
        "choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }

    with _stream_ctx(text=_sse(chunk, done=True)):
        events = await _collect(adapter, _simple_request())

    assert len([e for e in events if isinstance(e, StreamTextDelta)]) == 1
    assert len([e for e in events if isinstance(e, StreamMessageStop)]) == 1
    assert len([e for e in events if isinstance(e, StreamUsage)]) == 1
    assert len(events) == 3


# ── 4. finish_reason → StreamMessageStop mapping ─────────────────────────


async def test_stream_finish_reason_length() -> None:
    adapter = _adapter()
    chunk: dict[str, Any] = {
        "choices": [{"delta": {"content": "truncated"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 100},
    }

    with _stream_ctx(text=_sse(chunk)):
        events = await _collect(adapter, _simple_request())

    stop = next(e for e in events if isinstance(e, StreamMessageStop))
    assert stop.stop_reason == "max_tokens"


async def test_stream_finish_reason_tool_calls() -> None:
    adapter = _adapter()
    chunk1: dict[str, Any] = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "id": "x",
                            "type": "function",
                            "function": {"name": "fn", "arguments": "{}"},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    chunk2: dict[str, Any] = {
        "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }

    with _stream_ctx(text=_sse(chunk1, chunk2)):
        events = await _collect(adapter, _simple_request())

    stop = next(e for e in events if isinstance(e, StreamMessageStop))
    assert stop.stop_reason == "tool_use"


# ── 5. 4xx errors before stream → ProviderAPIError ────────────────────────


async def test_stream_http_error_raises_provider_api_error() -> None:
    adapter = _adapter()
    with (
        _stream_ctx(text="unauthorized", status=401),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        gen = await adapter.stream(_simple_request())
        async for _ in gen:
            pass
    assert exc_info.value.status_code == 401
    assert "unauthorized" in exc_info.value.body


async def test_stream_429_raises() -> None:
    adapter = _adapter()
    with (
        _stream_ctx(text="rate limited", status=429),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        gen = await adapter.stream(_simple_request())
        async for _ in gen:
            pass
    assert exc_info.value.status_code == 429


# ── 6. cancel mid-stream → loop stops ────────────────────────────────────


async def test_stream_cancel_stops_early() -> None:
    """Pre-set cancel event; no text deltas should arrive."""
    adapter = _adapter()
    cancel = asyncio.Event()
    cancel.set()

    chunk: dict[str, Any] = {"choices": [{"delta": {"content": "A"}, "finish_reason": None}]}
    with _stream_ctx(text=_sse(chunk)):
        gen: AsyncIterator[Any] = await adapter.stream(_simple_request(), cancel=cancel)
        events: list[Any] = []
        async for ev in gen:
            events.append(ev)

    assert len([e for e in events if isinstance(e, StreamTextDelta)]) == 0


# ── 7. cost computed from pricing table ──────────────────────────────────


async def test_stream_cost_computed_from_pricing_table() -> None:
    adapter = _adapter()
    chunk: dict[str, Any] = {
        "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
    }

    with _stream_ctx(text=_sse(chunk)):
        events = await _collect(adapter, _simple_request(model="gpt-4o"))

    usage = next(e for e in events if isinstance(e, StreamUsage))
    assert usage.cost_usd is not None
    expected = estimate_openai_cost("gpt-4o", 1000, 500)
    assert abs(usage.cost_usd - expected) < 1e-9


# ── 8. protocol conformance ───────────────────────────────────────────────


def test_openai_adapter_satisfies_supports_streaming() -> None:
    assert isinstance(_adapter(), SupportsStreaming)
