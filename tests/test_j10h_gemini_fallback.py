"""J10h — Gemini rate-limit fallback net.

Unit tests for:
  (a) Gemini adapter raises GeminiRateLimitError on 429 (and 503).
  (b) complete_with_fallback falls through to claude-code on GeminiRateLimitError.
  (c) complete_with_fallback returns gemini result directly (no claude call) on success.
  (d) complete_with_fallback re-raises on a non-retryable gemini 400.
  (e) lm-studio is never in any fallback path.

All HTTP is mocked — no real network traffic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock, Usage
from artemis.providers.errors import GeminiRateLimitError, ProviderAPIError
from artemis.providers.fallback import complete_with_fallback
from artemis.providers.gemini.adapter import GeminiAdapter

pytestmark = pytest.mark.asyncio

# ── Helpers ────────────────────────────────────────────────────────────────────


def _gemini_adapter(api_key: str = "test-gemini-key") -> GeminiAdapter:
    return GeminiAdapter(api_key=api_key)


def _make_request() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=[TextBlock(text="Hello")])],
        max_tokens=64,
    )


def _success_response_data() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hi from Gemini"}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 8},
    }


def _mock_gemini_http(status: int, body: dict[str, Any] | None = None) -> Any:
    """Patch httpx.AsyncClient.post to return a mock response."""
    resp_body = body or {}
    mock_response = httpx.Response(status, json=resp_body)
    return patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response)


def _fake_completion_response(text: str = "Hi from claude-code") -> Any:
    """Build a minimal CompletionResponse-like object for mocking."""
    from artemis.agent.client import CompletionResponse

    return CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=text)]),
        stop_reason="end_turn",
        usage=Usage(
            input_tokens=10,
            output_tokens=5,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
    )


# ── (a) Gemini adapter raises GeminiRateLimitError on 429 ─────────────────────


async def test_gemini_adapter_raises_rate_limit_error_on_429() -> None:
    """GeminiAdapter.complete() raises GeminiRateLimitError on HTTP 429."""
    adapter = _gemini_adapter()
    request = _make_request()

    with (
        _mock_gemini_http(429, {"error": "rate limited"}),
        pytest.raises(GeminiRateLimitError) as exc_info,
    ):
        await adapter.complete(request)

    assert exc_info.value.status_code == 429
    # GeminiRateLimitError IS-A ProviderAPIError
    assert isinstance(exc_info.value, ProviderAPIError)


async def test_gemini_adapter_raises_rate_limit_error_on_503() -> None:
    """GeminiAdapter.complete() raises GeminiRateLimitError on HTTP 503 (overloaded)."""
    adapter = _gemini_adapter()
    request = _make_request()

    with (
        _mock_gemini_http(503, {"error": "Service Unavailable"}),
        pytest.raises(GeminiRateLimitError) as exc_info,
    ):
        await adapter.complete(request)

    assert exc_info.value.status_code == 503
    assert isinstance(exc_info.value, ProviderAPIError)


async def test_gemini_adapter_raises_plain_api_error_on_400() -> None:
    """GeminiAdapter.complete() raises plain ProviderAPIError (not GeminiRateLimitError) on 400."""
    adapter = _gemini_adapter()
    request = _make_request()

    with (
        _mock_gemini_http(400, {"error": {"message": "bad request"}}),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await adapter.complete(request)

    assert exc_info.value.status_code == 400
    # Must NOT be the subclass
    assert type(exc_info.value) is ProviderAPIError


# ── (b) complete_with_fallback falls through on GeminiRateLimitError ──────────


async def test_fallback_triggered_on_gemini_rate_limit() -> None:
    """complete_with_fallback falls through to claude-code when Gemini returns 429."""
    request = _make_request()
    claude_response = _fake_completion_response("Hi from claude-code")

    with _mock_gemini_http(429, {"error": "rate limited"}):
        # Mock the claude-code adapter's complete() to return the canned response.
        mock_cc_adapter = MagicMock()
        mock_cc_adapter.complete = AsyncMock(return_value=claude_response)

        with patch(
            "artemis.providers.fallback.get_adapter",
            side_effect=lambda provider_id, **_kw: (
                _gemini_adapter() if provider_id == "gemini" else mock_cc_adapter
            ),
        ):
            serving: list[str] = []
            result = await complete_with_fallback(
                request,
                primary="gemini",
                fallback="claude-code",
                serving_provider_out=serving,
            )

    # Should have gotten the claude-code response.
    assert result.message.content[0].text == "Hi from claude-code"  # type: ignore[union-attr]
    assert serving == ["claude-code"]


async def test_fallback_triggered_on_gemini_503() -> None:
    """complete_with_fallback falls through on Gemini 503 (UNAVAILABLE)."""
    request = _make_request()
    claude_response = _fake_completion_response("Hi from claude after 503")

    with _mock_gemini_http(503, {"error": "overloaded"}):
        mock_cc_adapter = MagicMock()
        mock_cc_adapter.complete = AsyncMock(return_value=claude_response)

        with patch(
            "artemis.providers.fallback.get_adapter",
            side_effect=lambda provider_id, **_kw: (
                _gemini_adapter() if provider_id == "gemini" else mock_cc_adapter
            ),
        ):
            result = await complete_with_fallback(
                request,
                primary="gemini",
                fallback="claude-code",
            )

    assert result.message.content[0].text == "Hi from claude after 503"  # type: ignore[union-attr]


# ── (c) Gemini success — claude-code never called ─────────────────────────────


async def test_no_fallback_on_gemini_success() -> None:
    """When Gemini succeeds, claude-code adapter is never constructed."""
    request = _make_request()

    mock_cc_adapter = MagicMock()
    mock_cc_adapter.complete = AsyncMock()  # should never be called

    with (
        _mock_gemini_http(200, _success_response_data()),
        patch(
            "artemis.providers.fallback.get_adapter",
            side_effect=lambda provider_id, **_kw: (
                _gemini_adapter() if provider_id == "gemini" else mock_cc_adapter
            ),
        ),
    ):
        serving: list[str] = []
        result = await complete_with_fallback(
            request,
            primary="gemini",
            fallback="claude-code",
            serving_provider_out=serving,
        )

    # Claude-code adapter was never called.
    mock_cc_adapter.complete.assert_not_called()
    # Gemini response was returned.
    assert result.message.content[0].text == "Hi from Gemini"  # type: ignore[union-attr]
    assert serving == ["gemini"]


# ── (d) Non-retryable 400 re-raises; no fallthrough ──────────────────────────


async def test_no_fallback_on_non_retryable_400() -> None:
    """complete_with_fallback re-raises a gemini 400 — it's a bug, not a blip."""
    request = _make_request()

    mock_cc_adapter = MagicMock()
    mock_cc_adapter.complete = AsyncMock()  # should never be called

    with (
        _mock_gemini_http(400, {"error": {"message": "bad request"}}),
        patch(
            "artemis.providers.fallback.get_adapter",
            side_effect=lambda provider_id, **_kw: (
                _gemini_adapter() if provider_id == "gemini" else mock_cc_adapter
            ),
        ),
        pytest.raises(ProviderAPIError) as exc_info,
    ):
        await complete_with_fallback(
            request,
            primary="gemini",
            fallback="claude-code",
        )

    assert exc_info.value.status_code == 400
    # No fallthrough — claude-code was never called.
    mock_cc_adapter.complete.assert_not_called()


# ── (e) lm-studio never in any cascade ───────────────────────────────────────


async def test_lm_studio_forbidden_as_fallback() -> None:
    """Passing lm-studio as fallback raises ValueError immediately."""
    request = _make_request()

    with pytest.raises(ValueError, match="lm-studio"):
        await complete_with_fallback(
            request,
            primary="gemini",
            fallback="lm-studio",
        )


async def test_lm_studio_not_in_default_cascade() -> None:
    """complete_with_fallback has no path that defaults the fallback to lm-studio.
    The ValueError guard forbidding it must exist, and the default= parameter
    for ``fallback`` must be 'claude-code', not 'lm-studio'."""
    import inspect

    from artemis.providers import fallback as fallback_module

    source = inspect.getsource(fallback_module)
    # The guard that forbids lm-studio must exist.
    assert "lm-studio" in source, "lm-studio guard text must remain in fallback.py"
    assert "ValueError" in source, "ValueError guard must exist"
    # The default fallback= parameter must be claude-code, not lm-studio.
    assert 'fallback: str = "lm-studio"' not in source, "fallback= must never default to lm-studio"
    # The function must default fallback to claude-code.
    assert 'fallback: str = "claude-code"' in source, "fallback= should default to claude-code"


# ── Passthrough when primary == fallback ──────────────────────────────────────


async def test_passthrough_when_primary_equals_fallback() -> None:
    """When primary == fallback (agent already on claude-code), a single direct call is made."""
    request = _make_request()
    cc_response = _fake_completion_response("Hi from claude passthrough")

    mock_cc_adapter = MagicMock()
    mock_cc_adapter.complete = AsyncMock(return_value=cc_response)

    with patch(
        "artemis.providers.fallback.resolve_adapter_async",
        new_callable=AsyncMock,
        return_value=mock_cc_adapter,
    ):
        serving: list[str] = []
        result = await complete_with_fallback(
            request,
            primary="claude-code",
            fallback="claude-code",
            serving_provider_out=serving,
        )

    assert result.message.content[0].text == "Hi from claude passthrough"  # type: ignore[union-attr]
    mock_cc_adapter.complete.assert_called_once()
    assert serving == ["claude-code"]


# ── Construction-time fallthrough ─────────────────────────────────────────────


async def test_construction_error_falls_through_to_fallback() -> None:
    """MissingApiKeyError at Gemini construction falls through to claude-code."""
    from artemis.providers.errors import MissingApiKeyError

    request = _make_request()
    cc_response = _fake_completion_response("Hi from claude after key missing")

    mock_cc_adapter = MagicMock()
    mock_cc_adapter.complete = AsyncMock(return_value=cc_response)

    def _side_effect(provider_id: str, **kw: Any) -> Any:
        if provider_id == "gemini":
            raise MissingApiKeyError("GEMINI_API_KEY not set")
        return mock_cc_adapter

    with patch("artemis.providers.fallback.get_adapter", side_effect=_side_effect):
        serving: list[str] = []
        result = await complete_with_fallback(
            request,
            primary="gemini",
            fallback="claude-code",
            serving_provider_out=serving,
        )

    assert result.message.content[0].text == "Hi from claude after key missing"  # type: ignore[union-attr]
    assert serving == ["claude-code"]
