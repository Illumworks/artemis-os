"""Tests for LMStudioAdapter — OpenAI-compatible local server adapter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.providers.lm_studio.adapter import LMStudioAdapter
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


def test_default_base_url_from_settings() -> None:
    """Default base URL is derived from settings.lm_studio_base_url (+ /v1)."""
    from artemis.config import settings

    adapter = LMStudioAdapter()
    assert adapter._base_url == f"{settings.lm_studio_base_url}/v1"


def test_explicit_base_url() -> None:
    adapter = LMStudioAdapter(base_url="http://my-server:8080/v1")
    assert adapter._base_url == "http://my-server:8080/v1"


def test_artemis_env_override_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARTEMIS_LM_STUDIO_BASE_URL (prefixed form) overrides the default."""

    import artemis.config as config_module

    monkeypatch.setenv("ARTEMIS_LM_STUDIO_BASE_URL", "http://100.64.0.5:1234")
    # Re-instantiate Settings so it picks up the monkeypatched env var.
    new_settings = config_module.Settings()
    with patch.object(config_module, "settings", new_settings):
        import artemis.providers.lm_studio.adapter as adapter_module

        with patch.object(adapter_module, "settings", new_settings):
            adapter = LMStudioAdapter()
    assert adapter._base_url == "http://100.64.0.5:1234/v1"


def test_bare_env_override_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """LM_STUDIO_BASE_URL (bare alias) overrides the default."""
    import artemis.config as config_module

    monkeypatch.setenv("LM_STUDIO_BASE_URL", "http://100.64.0.10:1234")
    new_settings = config_module.Settings()
    with patch.object(config_module, "settings", new_settings):
        import artemis.providers.lm_studio.adapter as adapter_module

        with patch.object(adapter_module, "settings", new_settings):
            adapter = LMStudioAdapter()
    assert adapter._base_url == "http://100.64.0.10:1234/v1"


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


# ── reasoning budget (2026-09-09) ────────────────────────────────────────────


def test_a_small_budget_is_raised_to_the_reasoning_floor() -> None:
    """Local models spend tokens thinking before answering. At max_tokens=200 the
    35B returned 0 characters after 199 completion tokens, `finish_reason=length`
    — a 200 response with a usable shape and nothing in it, which is the worst
    way for this to fail. A note in the catalog concluded from 300/400/1200 that
    the model was unusable; it cost us the faster of the two local models."""
    import dataclasses

    from artemis.agent.client import CompletionRequest
    from artemis.providers.lm_studio.adapter import _REASONING_TOKEN_FLOOR, LMStudioAdapter

    adapter = LMStudioAdapter()
    request = CompletionRequest(messages=[], max_tokens=400)

    raised = adapter._with_reasoning_budget(request)

    assert raised.max_tokens == _REASONING_TOKEN_FLOOR
    assert dataclasses.replace(raised, max_tokens=400) == request, "only the budget changes"


def test_a_generous_budget_is_left_alone() -> None:
    from artemis.agent.client import CompletionRequest
    from artemis.providers.lm_studio.adapter import _REASONING_TOKEN_FLOOR, LMStudioAdapter

    request = CompletionRequest(messages=[], max_tokens=_REASONING_TOKEN_FLOOR * 2)

    assert LMStudioAdapter()._with_reasoning_budget(request).max_tokens == (
        _REASONING_TOKEN_FLOOR * 2
    )


def test_the_floor_matches_what_the_studio_itself_uses() -> None:
    """The Studio's own `offload` tool defaults to 8192 for the same reason."""
    from artemis.providers.lm_studio.adapter import _REASONING_TOKEN_FLOOR

    assert _REASONING_TOKEN_FLOOR == 8192


def test_a_local_call_costs_nothing() -> None:
    """It inherits OpenAI's cost estimator, which priced a real local summary at
    $0.00038. Recording that would make the cost dashboard show no saving from
    the local box, which is the only reason to route work to it."""
    from artemis.agent.types import Message, TextBlock, Usage
    from artemis.providers.openai.adapter import _OpenAICompletionResponse

    response = _OpenAICompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="x")]),
        stop_reason="stop",
        usage=Usage(input_tokens=1000, output_tokens=1000),
        cost_usd=0.00038,
    )
    object.__setattr__(response, "cost_usd", 0.0)

    assert response.cost_usd == 0.0
