"""Tests for artemis.costs.pricing — the central rate registry."""

from __future__ import annotations

import pytest

from artemis.costs.pricing import get_rates


def test_anthropic_opus_rates() -> None:
    """get_rates returns expected per-million rates for claude-opus-4-7."""
    rates = get_rates("anthropic", "claude-opus-4-7")
    assert rates["input"] == 15.0
    assert rates["output"] == 75.0
    assert rates["cache_write"] == 18.75
    assert rates["cache_read"] == 1.50


def test_anthropic_sonnet_rates() -> None:
    """get_rates returns expected rates for claude-sonnet-4-6."""
    rates = get_rates("anthropic", "claude-sonnet-4-6")
    assert rates["input"] == 3.0
    assert rates["output"] == 15.0
    assert rates["cache_write"] == 3.75
    assert rates["cache_read"] == 0.30


def test_unknown_model_raises_key_error() -> None:
    """Unknown model raises KeyError — callers must handle."""
    with pytest.raises(KeyError):
        get_rates("openai", "gpt-99-ultra-super")


def test_unknown_provider_raises_key_error() -> None:
    """Unknown provider raises KeyError."""
    with pytest.raises(KeyError):
        get_rates("acme-ai", "acme-model-1")


def test_gemini_cache_rates_are_zero() -> None:
    """Gemini models have zero cache rates (no prompt caching support)."""
    rates = get_rates("gemini", "gemini-2.5-flash-preview-05-20")
    assert rates["cache_write"] == 0.0
    assert rates["cache_read"] == 0.0


def test_openai_cache_rates_are_zero() -> None:
    """OpenAI models have zero cache rates in our registry."""
    rates = get_rates("openai", "gpt-4o")
    assert rates["cache_write"] == 0.0
    assert rates["cache_read"] == 0.0


def test_claude_code_delegates_to_anthropic() -> None:
    """claude-code provider falls through to anthropic rates for the same model."""
    claude_code_rates = get_rates("claude-code", "claude-sonnet-4-6")
    anthropic_rates = get_rates("anthropic", "claude-sonnet-4-6")
    assert claude_code_rates == anthropic_rates


def test_anthropic_prefix_fallback() -> None:
    """Unknown claude-opus-X version falls back to claude-opus rates."""
    rates = get_rates("anthropic", "claude-opus-99-hypothetical")
    assert rates["input"] == 15.0  # opus prefix fallback
    assert rates["output"] == 75.0


def test_lm_studio_zero_rates() -> None:
    """lm-studio returns zero rates (local inference)."""
    rates = get_rates("lm-studio", "any-local-model")
    assert rates == {"input": 0.0, "output": 0.0, "cache_write": 0.0, "cache_read": 0.0}


def test_get_rates_is_cached() -> None:
    """Repeated calls return the same object (lru_cache active)."""
    r1 = get_rates("anthropic", "claude-sonnet-4-6")
    r2 = get_rates("anthropic", "claude-sonnet-4-6")
    assert r1 is r2
