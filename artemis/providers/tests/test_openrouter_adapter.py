"""Tests for the OpenRouter provider adapter.

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
from artemis.providers.openrouter.adapter import OpenRouterAdapter
from artemis.providers.openrouter.models import (
    OPENROUTER_MODEL_MAP,
    resolve_model,
)

pytestmark = pytest.mark.asyncio

# ── helpers ────────────────────────────────────────────────────────────────


def _make_adapter(api_key: str = "test-or-key", **kwargs: Any) -> OpenRouterAdapter:
    return OpenRouterAdapter(api_key=api_key, **kwargs)


def _text_response(
    text: str,
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_cost: float | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }
    if total_cost is not None:
        usage["total_cost"] = total_cost
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": text, "tool_calls": None},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
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
        assert "openrouter.ai" in url
        assert "chat/completions" in url

        headers: dict[str, str] = call_args.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer test-or-key"
        assert headers["HTTP-Referer"] == "https://artemis.local"
        assert headers["X-Title"] == "Artemis"

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


# ── 3. tools definition in request ────────────────────────────────────────


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


# ── 4. tool result round-trip ─────────────────────────────────────────────


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


# ── 5. system prompt ──────────────────────────────────────────────────────


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


# ── 6. API 400 error ──────────────────────────────────────────────────────


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


# ── 7. 429 rate limit ─────────────────────────────────────────────────────


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


# ── 8. missing API key ────────────────────────────────────────────────────


def test_missing_api_key_raises_at_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        OpenRouterAdapter()


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    adapter = OpenRouterAdapter(api_key="my-key")
    assert adapter._api_key == "my-key"


# ── 9. resolve_model ──────────────────────────────────────────────────────


def test_resolve_model_alias() -> None:
    assert resolve_model("gpt-4o") == "openai/gpt-4o"
    assert resolve_model("claude-3.5-sonnet") == "anthropic/claude-3.5-sonnet"
    assert resolve_model("llama-3.3-70b-free") == "meta-llama/llama-3.3-70b-instruct:free"


def test_resolve_model_passthrough_unknown() -> None:
    # Full OpenRouter IDs pass through unchanged
    assert resolve_model("custom/my-model") == "custom/my-model"


def test_resolve_model_none_returns_default() -> None:
    result = resolve_model(None)
    assert result == "meta-llama/llama-3.3-70b-instruct:free"


def test_all_map_aliases_resolve() -> None:
    for alias, full_id in OPENROUTER_MODEL_MAP.items():
        assert resolve_model(alias) == full_id


# ── 10. cost from response ────────────────────────────────────────────────


async def test_cost_usd_from_response_total_cost() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )
    resp_data = _text_response("ok", total_cost=0.00042)

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert hasattr(response, "cost_usd")
    assert abs(response.cost_usd - 0.00042) < 1e-9


async def test_cost_usd_defaults_to_zero_when_absent() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )
    resp_data = _text_response("ok")  # no total_cost field

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert hasattr(response, "cost_usd")
    assert response.cost_usd == 0.0


# ── 11. stop_reason mapping ───────────────────────────────────────────────


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


# ── 12. max_tokens forwarded ──────────────────────────────────────────────


async def test_max_tokens_forwarded_in_body() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        max_tokens=256,
    )

    with _mock_post(_text_response("ok")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert body["max_tokens"] == 256


# ── 13. model resolved on request.model ───────────────────────────────────


async def test_request_model_alias_resolved() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        model="gpt-4o-mini",
    )

    with _mock_post(_text_response("ok")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert body["model"] == "openai/gpt-4o-mini"
