"""Tests for the Gemini provider adapter.

Uses httpx.MockTransport to intercept HTTP calls — no real network traffic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock, Tool, ToolResultBlock, ToolUseBlock
from artemis.providers.errors import MissingApiKeyError, ProviderAPIError
from artemis.providers.gemini.adapter import GeminiAdapter
from artemis.providers.gemini.models import (
    GEMINI_MODEL_MAP,
    estimate_cost,
    resolve_model,
)

pytestmark = pytest.mark.asyncio

# ── helpers ────────────────────────────────────────────────────────────────


def _make_adapter(api_key: str = "test-key", **kwargs: Any) -> GeminiAdapter:
    return GeminiAdapter(api_key=api_key, **kwargs)


def _text_response(
    text: str,
    finish_reason: str = "STOP",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}], "role": "model"},
                "finishReason": finish_reason,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": input_tokens,
            "candidatesTokenCount": output_tokens,
        },
    }


def _tool_call_response(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"functionCall": {"name": name, "args": args}}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8},
    }


def _mock_response(data: dict[str, Any], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


def _mock_post(response_data: dict[str, Any], status: int = 200) -> Any:
    """Patch httpx.AsyncClient.post to return a mock response."""
    mock_response = _mock_response(response_data, status)
    return patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response)


# ── 1. simple text completion ──────────────────────────────────────────────


async def test_complete_simple_text() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hello")])],
    )
    resp_data = _text_response("Hi there!")

    with _mock_post(resp_data) as mock_post:
        response = await adapter.complete(request)

        # Verify URL contains model and key
        call_args = mock_post.call_args
        url: str = call_args.args[0] if call_args.args else call_args.kwargs["url"]
        assert "generateContent" in url
        assert "test-key" in url

        # Verify Content-Type header
        headers: dict[str, str] = call_args.kwargs.get("headers", {})
        assert headers.get("Content-Type") == "application/json"

        # Verify body shape
        body: dict[str, Any] = call_args.kwargs.get("json", {})
        assert "contents" in body
        assert body["contents"][0]["role"] == "user"
        assert body["contents"][0]["parts"][0]["text"] == "Hello"

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
            Message(role="user", content=[TextBlock(text="First question")]),
            Message(role="assistant", content=[TextBlock(text="First answer")]),
            Message(role="user", content=[TextBlock(text="Second question")]),
        ],
    )

    with _mock_post(_text_response("Second answer")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        contents = body["contents"]
        assert len(contents) == 3
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"  # assistant -> model
        assert contents[2]["role"] == "user"
        assert contents[1]["parts"][0]["text"] == "First answer"


# ── 3. tool definitions in request ────────────────────────────────────────


async def test_complete_with_tools_definition() -> None:
    adapter = _make_adapter()
    tool = Tool(
        name="search",
        description="Search the web",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Search for cats")])],
        tools=[tool],
    )

    with _mock_post(_tool_call_response("search", {"query": "cats"})) as mock_post:
        response = await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert "tools" in body
        fn_decls = body["tools"][0]["functionDeclarations"]
        assert fn_decls[0]["name"] == "search"
        assert fn_decls[0]["description"] == "Search the web"

    assert len(response.message.content) == 1
    block = response.message.content[0]
    assert isinstance(block, ToolUseBlock)
    assert block.name == "search"
    assert block.input == {"query": "cats"}
    assert block.id  # synthetic UUID assigned


# ── 4. tool result round-trip ─────────────────────────────────────────────


async def test_complete_with_tool_result() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[
            Message(role="user", content=[TextBlock(text="Use the tool")]),
            Message(
                role="assistant",
                content=[ToolUseBlock(id="call-1", name="search", input={"query": "cats"})],
            ),
            Message(
                role="user",
                content=[ToolResultBlock(tool_use_id="call-1", content="Results: cats are great")],
            ),
        ],
    )

    with _mock_post(_text_response("Based on results...")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        contents = body["contents"]
        # The tool result should appear as functionResponse in user-role content
        user_part_with_result = next(
            (
                c
                for c in contents
                if c["role"] == "user" and any("functionResponse" in p for p in c["parts"])
            ),
            None,
        )
        assert user_part_with_result is not None
        fn_resp = user_part_with_result["parts"][0]["functionResponse"]
        assert fn_resp["name"] == "call-1"


# ── 5. system prompt ──────────────────────────────────────────────────────


async def test_complete_with_system_prompt() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        system="You are a helpful assistant.",
    )

    with _mock_post(_text_response("Hello!")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert "systemInstruction" in body
        assert body["systemInstruction"]["parts"][0]["text"] == "You are a helpful assistant."


# ── 6. API 400 error ──────────────────────────────────────────────────────


async def test_complete_raises_provider_api_error_on_400() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )

    with (
        _mock_post({"error": {"message": "bad request"}}, status=400),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(request)

    assert exc_info.value.status_code == 400
    assert "bad request" in exc_info.value.body


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
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        GeminiAdapter()


def test_explicit_api_key_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    adapter = GeminiAdapter(api_key="explicit-key")
    assert adapter._api_key == "explicit-key"


# ── 9. resolve_model ──────────────────────────────────────────────────────


def test_resolve_model_alias() -> None:
    assert resolve_model("gemini-pro") == "gemini-1.5-pro"
    assert resolve_model("gemini-flash-2") == "gemini-2.0-flash"


def test_resolve_model_passthrough_unknown() -> None:
    assert resolve_model("gemini-custom-experimental") == "gemini-custom-experimental"


def test_resolve_model_none_returns_default() -> None:
    result = resolve_model(None)
    # Should return the resolved default (gemini-2.5-flash-preview or the default model)
    assert result  # non-empty string


def test_all_map_aliases_resolve_to_different_ids() -> None:
    for alias, full_id in GEMINI_MODEL_MAP.items():
        assert resolve_model(alias) == full_id


# ── 10. cost estimation ───────────────────────────────────────────────────


def test_estimate_cost_known_model() -> None:
    # gemini-1.5-pro: 0.00125 per 1k input, 0.005 per 1k output
    cost = estimate_cost("gemini-1.5-pro", input_tokens=1000, output_tokens=1000)
    assert abs(cost - (0.00125 + 0.005)) < 1e-9


def test_estimate_cost_flash() -> None:
    # gemini-1.5-flash: 0.000075 per 1k input, 0.0003 per 1k output
    cost = estimate_cost("gemini-1.5-flash", input_tokens=2000, output_tokens=500)
    expected = (2000 / 1000) * 0.000075 + (500 / 1000) * 0.0003
    assert abs(cost - expected) < 1e-9


def test_estimate_cost_unknown_model_falls_back_to_flash() -> None:
    cost_unknown = estimate_cost("gemini-unknown-model", input_tokens=1000, output_tokens=1000)
    cost_flash = estimate_cost("gemini-2.0-flash", input_tokens=1000, output_tokens=1000)
    assert abs(cost_unknown - cost_flash) < 1e-9


def test_estimate_cost_zero_tokens() -> None:
    assert estimate_cost("gemini-2.0-flash", input_tokens=0, output_tokens=0) == 0.0


# ── 11. stop_reason mapping ───────────────────────────────────────────────


async def test_stop_reason_max_tokens() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )
    resp_data = _text_response("truncated...", finish_reason="MAX_TOKENS")

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert response.stop_reason == "max_tokens"


async def test_stop_reason_safety() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )
    resp_data = _text_response("", finish_reason="SAFETY")

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert response.stop_reason == "safety"


async def test_stop_reason_unknown_lowercased() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )
    resp_data = _text_response("ok", finish_reason="RECITATION")

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    assert response.stop_reason == "recitation"


# ── 12. cost exposed on response ──────────────────────────────────────────


async def test_cost_usd_on_response() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
    )
    resp_data = _text_response("ok", input_tokens=1000, output_tokens=1000)

    with _mock_post(resp_data):
        response = await adapter.complete(request)

    # The response subclass exposes cost_usd
    assert hasattr(response, "cost_usd")
    expected = estimate_cost("gemini-2.5-flash-preview-05-20", 1000, 1000)
    assert abs(response.cost_usd - expected) < 1e-9


# ── 13. max_tokens forwarded ──────────────────────────────────────────────


async def test_max_tokens_forwarded_to_generation_config() -> None:
    adapter = _make_adapter()
    request = CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hi")])],
        max_tokens=512,
    )

    with _mock_post(_text_response("ok")) as mock_post:
        await adapter.complete(request)

        body = mock_post.call_args.kwargs["json"]
        assert body["generationConfig"]["maxOutputTokens"] == 512
