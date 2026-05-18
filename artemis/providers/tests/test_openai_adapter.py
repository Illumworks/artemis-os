"""Tests for the OpenAI provider adapter.

Uses unittest.mock to patch httpx.AsyncClient.post — no real network traffic.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock, Tool, ToolResultBlock, ToolUseBlock
from artemis.providers.errors import MissingApiKeyError, ProviderAPIError
from artemis.providers.openai.adapter import OpenAIAdapter
from artemis.providers.openai.models import (
    OPENAI_MODEL_MAP,
    estimate_openai_cost,
    resolve_openai_model,
)

pytestmark = pytest.mark.asyncio

# ── helpers ────────────────────────────────────────────────────────────────


def _make_adapter(api_key: str = "test-openai-key", **kwargs: Any) -> OpenAIAdapter:
    return OpenAIAdapter(api_key=api_key, **kwargs)


def _text_response(
    text: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": text, "tool_calls": None},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _tool_call_response(tool_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
    }


def _mock_post(response_data: dict[str, Any], status: int = 200) -> Any:
    """Patch httpx.AsyncClient.post to return a mock response."""
    mock_response = httpx.Response(status, json=response_data)
    return patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response)


# ── 1. simple text completion ──────────────────────────────────────────────


async def test_complete_simple_text() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hello")])],
    )

    with _mock_post(_text_response("Hi there!")) as mock_post:
        response = await adapter.complete(request)

        call_args = mock_post.call_args
        url: str = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "api.openai.com" in url
        assert "chat/completions" in url

        headers: dict[str, str] = call_args.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer test-openai-key"
        # No OpenRouter-specific headers
        assert "HTTP-Referer" not in headers
        assert "X-Title" not in headers

        body: dict[str, Any] = call_args.kwargs.get("json", {})
        assert body["messages"][-1]["role"] == "user"
        assert body["messages"][-1]["content"] == "Hello"

    assert response.stop_reason == "end_turn"
    assert len(response.message.content) == 1
    block = response.message.content[0]
    assert isinstance(block, TextBlock)
    assert block.text == "Hi there!"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5


# ── 2. multi-turn history ─────────────────────────────────────────────────


async def test_complete_multi_turn_history() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[
            Message(role="user", content=[TextBlock(text="First")]),
            Message(role="assistant", content=[TextBlock(text="Second")]),
            Message(role="user", content=[TextBlock(text="Third")]),
        ],
    )

    with _mock_post(_text_response("Reply")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        msgs = body["messages"]
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant", "user"]
        assert msgs[0]["content"] == "First"
        assert msgs[1]["content"] == "Second"
        assert msgs[2]["content"] == "Third"


# ── 3. system prompt ──────────────────────────────────────────────────────


async def test_complete_with_system_prompt() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        system="You are a calculator.",
    )

    with _mock_post(_text_response("Hello!")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        first_msg = body["messages"][0]
        assert first_msg["role"] == "system"
        assert first_msg["content"] == "You are a calculator."


# ── 4. tools in request ───────────────────────────────────────────────────


async def test_complete_with_tools_definition() -> None:
    adapter = _make_adapter()
    tool = Tool(
        name="calculator",
        description="Perform arithmetic",
        input_schema={"type": "object", "properties": {"expr": {"type": "string"}}},
    )
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="What is 2+2?")])],
        tools=[tool],
    )

    with _mock_post(_tool_call_response("call-1", "calculator", {"expr": "2+2"})) as mock_post:
        response = await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert "tools" in body
        fn = body["tools"][0]
        assert fn["type"] == "function"
        assert fn["function"]["name"] == "calculator"

    assert len(response.message.content) == 1
    block = response.message.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.name == "calculator"
    assert block.input == {"expr": "2+2"}
    assert block.id == "call-1"


# ── 5. tool_use response parsing ──────────────────────────────────────────


async def test_complete_tool_use_response_parsed() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Search something")])],
    )
    resp = _tool_call_response("call-xyz", "search", {"query": "cats"})

    with _mock_post(resp):
        response = await adapter.complete(request)

    assert len(response.message.content) == 1
    block = response.message.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.id == "call-xyz"
    assert block.name == "search"
    assert block.input == {"query": "cats"}
    assert response.stop_reason == "tool_use"


# ── 6. tool_result message handling ──────────────────────────────────────


async def test_complete_with_tool_result() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[
            Message(role="user", content=[TextBlock(text="Use the tool")]),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="call-1", name="calculator", input={"expr": "2+2"})],
            ),
            Message(
                role="user",
                content=[ToolResultBlock(tool_use_id="call-1", content="4")],
            ),
        ],
    )

    with _mock_post(_text_response("The answer is 4")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        msgs = body["messages"]

        # Tool result should appear as role: "tool"
        tool_msg = next((m for m in msgs if m.get("role") == "tool"), None)
        assert tool_msg is not None
        assert tool_msg["tool_call_id"] == "call-1"

        # The assistant turn should have tool_calls
        assistant_msg = next((m for m in msgs if m.get("role") == "assistant"), None)
        assert assistant_msg is not None
        assert assistant_msg.get("tool_calls")


# ── 7. usage + cost_usd computation ──────────────────────────────────────


async def test_cost_usd_computed_for_gpt4o() -> None:
    adapter = _make_adapter(default_model="gpt-4o")
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        model="gpt-4o",
    )
    resp_data = _text_response("ok", prompt_tokens=1000, completion_tokens=500)

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert hasattr(response, "cost_usd")
    expected = estimate_openai_cost("gpt-4o", 1000, 500)
    assert abs(response.cost_usd - expected) < 1e-9


async def test_cost_usd_computed_for_gpt4o_mini() -> None:
    adapter = _make_adapter(default_model="gpt-4o-mini")
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        model="gpt-4o-mini",
    )
    resp_data = _text_response("ok", prompt_tokens=2000, completion_tokens=300)

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert hasattr(response, "cost_usd")
    expected = estimate_openai_cost("gpt-4o-mini", 2000, 300)
    assert abs(response.cost_usd - expected) < 1e-9


# ── 8. max_tokens vs max_completion_tokens ────────────────────────────────


async def test_gpt_model_uses_max_tokens() -> None:
    """Non-o-series models get max_tokens in the request body."""
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        model="gpt-5",
        max_tokens=256,
    )

    with _mock_post(_text_response("ok")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert "max_tokens" in body
        assert body["max_tokens"] == 256
        assert "max_completion_tokens" not in body


async def test_o_series_model_uses_max_completion_tokens() -> None:
    """o-series models (o3, o4-mini …) get max_completion_tokens, not max_tokens."""
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        model="o3",
        max_tokens=512,
    )

    with _mock_post(_text_response("ok")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert "max_completion_tokens" in body
        assert body["max_completion_tokens"] == 512
        assert "max_tokens" not in body


async def test_o3_mini_uses_max_completion_tokens() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        model="o3-mini",
        max_tokens=128,
    )

    with _mock_post(_text_response("ok")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert "max_completion_tokens" in body
        assert "max_tokens" not in body


# ── 9. API error handling ─────────────────────────────────────────────────


async def test_complete_raises_provider_api_error_on_400() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )

    with (
        _mock_post({"error": {"message": "invalid model"}}, status=400),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(request)

    assert exc_info.value.status_code == 400
    assert "invalid model" in exc_info.value.body


async def test_complete_raises_provider_api_error_on_429() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )

    with (
        _mock_post({"error": "rate limited"}, status=429),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(request)

    assert exc_info.value.status_code == 429


# ── 10. missing API key ───────────────────────────────────────────────────


def test_missing_api_key_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        OpenAIAdapter()


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = OpenAIAdapter(api_key="my-key")
    assert adapter._api_key == "my-key"


# ── 11. resolve_openai_model ──────────────────────────────────────────────


def test_resolve_openai_model_known_alias() -> None:
    assert resolve_openai_model("gpt-4o") == "gpt-4o"
    assert resolve_openai_model("gpt-4o-mini") == "gpt-4o-mini"
    assert resolve_openai_model("o3") == "o3"


def test_resolve_openai_model_passthrough_unknown() -> None:
    # Unknown full model IDs pass through unchanged
    assert resolve_openai_model("gpt-4o-2024-11-20") == "gpt-4o-2024-11-20"
    assert resolve_openai_model("custom-model") == "custom-model"


def test_resolve_openai_model_none_returns_default() -> None:
    result = resolve_openai_model(None)
    assert result == "gpt-5-mini"


def test_all_map_aliases_resolve() -> None:
    for alias, full_id in OPENAI_MODEL_MAP.items():
        assert resolve_openai_model(alias) == full_id


# ── 12. pricing computation ───────────────────────────────────────────────


def test_estimate_cost_gpt4o() -> None:
    # gpt-4o: $0.0025/1k input, $0.01/1k output
    cost = estimate_openai_cost("gpt-4o", 1000, 1000)
    assert abs(cost - (0.0025 + 0.01)) < 1e-9


def test_estimate_cost_o3() -> None:
    # o3: $0.002/1k input, $0.008/1k output
    cost = estimate_openai_cost("o3", 2000, 1000)
    expected = (2000 / 1000) * 0.002 + (1000 / 1000) * 0.008
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_unknown_model_falls_back() -> None:
    # Unknown model should fall back to gpt-4o-mini pricing, not crash
    cost = estimate_openai_cost("nonexistent-model", 1000, 1000)
    expected = estimate_openai_cost("gpt-4o-mini", 1000, 1000)
    assert abs(cost - expected) < 1e-9


# ── 13. stop_reason mapping ───────────────────────────────────────────────


async def test_stop_reason_length() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )

    with _mock_post(_text_response("truncated", finish_reason="length")):
        response = await adapter.complete(request)

    assert response.stop_reason == "max_tokens"


async def test_stop_reason_tool_calls() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Use tool")])],
    )

    with _mock_post(_tool_call_response("c1", "fn", {})):
        response = await adapter.complete(request)

    assert response.stop_reason == "tool_use"


async def test_stop_reason_unknown_lowercased() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )

    with _mock_post(_text_response("ok", finish_reason="CONTENT_FILTER")):
        response = await adapter.complete(request)

    assert response.stop_reason == "content_filter"
