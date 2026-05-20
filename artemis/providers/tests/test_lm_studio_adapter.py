"""Tests for LMStudioAdapter — OpenAI-compatible local server adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.providers.lm_studio.adapter import _LM_STUDIO_DEFAULT_BASE, LMStudioAdapter
from artemis.providers.openai.adapter import OpenAIAdapter

pytestmark = pytest.mark.asyncio


def _simple_request(text: str = "Hello") -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text=text)])],
    )


# ── construction ──────────────────────────────────────────────────────────────


def test_inherits_from_openai_adapter() -> None:
    adapter = LMStudioAdapter()
    assert isinstance(adapter, OpenAIAdapter)


def test_default_base_url() -> None:
    adapter = LMStudioAdapter()
    assert adapter._base_url == _LM_STUDIO_DEFAULT_BASE


def test_explicit_base_url() -> None:
    adapter = LMStudioAdapter(base_url="http://my-server:8080/v1")
    assert adapter._base_url == "http://my-server:8080/v1"


def test_env_override_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://192.168.1.10:1234/v1")
    adapter = LMStudioAdapter()
    assert adapter._base_url == "http://192.168.1.10:1234/v1"


def test_no_api_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter = LMStudioAdapter()
    assert adapter._api_key == "not-needed-for-local-server"


def test_explicit_base_url_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://from-env/v1")
    adapter = LMStudioAdapter(base_url="http://explicit/v1")
    assert adapter._base_url == "http://explicit/v1"


# ── complete() uses the correct URL ──────────────────────────────────────────


async def test_complete_posts_to_correct_base_url() -> None:
    adapter = LMStudioAdapter(base_url="http://localhost:9999/v1")

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.is_success = True
    mock_response.json.return_value = {
        "choices": [{"message": {"content": "pong", "role": "assistant"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }

    captured_urls: list[str] = []

    class CapturingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            captured_urls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "pong", "role": "assistant"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3},
                },
            )

    with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=CapturingTransport())):
        await adapter.complete(_simple_request())

    assert any("9999" in url for url in captured_urls), f"Expected port 9999 in {captured_urls}"


# ── default model ──────────────────────────────────────────────────────────────


def test_explicit_default_model() -> None:
    adapter = LMStudioAdapter(default_model="meta-llama/Llama-3.3-70B")
    assert adapter._default_model == "meta-llama/Llama-3.3-70B"
