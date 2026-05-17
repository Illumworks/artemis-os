"""Tests for the provider registry."""

from __future__ import annotations

import pytest

from artemis.providers.errors import MissingApiKeyError, UnknownProviderError
from artemis.providers.gemini.adapter import GeminiAdapter
from artemis.providers.openrouter.adapter import OpenRouterAdapter
from artemis.providers.registry import get_adapter, list_providers

# ── list_providers ─────────────────────────────────────────────────────────


def test_list_providers_sorted() -> None:
    providers = list_providers()
    assert providers == sorted(providers)


def test_list_providers_contains_all_three() -> None:
    providers = list_providers()
    assert "anthropic" in providers
    assert "gemini" in providers
    assert "openrouter" in providers


# ── get_adapter ────────────────────────────────────────────────────────────


def test_get_adapter_gemini_returns_gemini_adapter() -> None:
    adapter = get_adapter("gemini", api_key="test-key")
    assert isinstance(adapter, GeminiAdapter)


def test_get_adapter_openrouter_returns_openrouter_adapter() -> None:
    adapter = get_adapter("openrouter", api_key="test-key")
    assert isinstance(adapter, OpenRouterAdapter)


def test_get_adapter_anthropic_returns_anthropic_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    from artemis.agent.client import AnthropicAdapter

    adapter = get_adapter("anthropic")
    assert isinstance(adapter, AnthropicAdapter)


def test_get_adapter_unknown_raises_unknown_provider_error() -> None:
    with pytest.raises(UnknownProviderError) as exc_info:
        get_adapter("nonsense")
    assert "nonsense" in str(exc_info.value)


def test_get_adapter_gemini_missing_key_raises_missing_api_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        get_adapter("gemini")


def test_get_adapter_openrouter_missing_key_raises_missing_api_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(MissingApiKeyError):
        get_adapter("openrouter")


def test_get_adapter_passes_kwargs_to_adapter() -> None:
    adapter = get_adapter("gemini", api_key="custom-key", default_model="gemini-flash")
    assert isinstance(adapter, GeminiAdapter)
    assert adapter._api_key == "custom-key"
    # gemini-flash resolves to gemini-1.5-flash
    assert adapter._default_model == "gemini-1.5-flash"


def test_get_adapter_openrouter_passes_kwargs() -> None:
    adapter = get_adapter("openrouter", api_key="my-key", default_model="gpt-4o")
    assert isinstance(adapter, OpenRouterAdapter)
    assert adapter._api_key == "my-key"
    assert adapter._default_model == "openai/gpt-4o"


# ── ModelAdapter protocol conformance ──────────────────────────────────────


def test_gemini_adapter_has_complete_method() -> None:
    adapter = GeminiAdapter(api_key="test")
    assert callable(getattr(adapter, "complete", None))


def test_openrouter_adapter_has_complete_method() -> None:
    adapter = OpenRouterAdapter(api_key="test")
    assert callable(getattr(adapter, "complete", None))
