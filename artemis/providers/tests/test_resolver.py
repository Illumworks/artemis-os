"""Tests for the provider cascade resolver."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from artemis.providers.errors import (
    MissingApiKeyError,
    MissingCliBinaryError,
    UnknownProviderError,
)
from artemis.providers.resolver import (
    DEFAULT_CASCADE,
    NoProviderAvailableError,
    resolve_adapter,
)


class _Sentinel:
    """Stand-in for a constructed ``ModelAdapter`` — identity comparisons only."""

    def __init__(self, name: str) -> None:
        self.name = name


def _make_builders(available: dict[str, Any]) -> Any:
    """Return a side_effect for ``get_adapter`` that returns sentinels for
    providers in ``available`` and raises the supplied exception otherwise."""

    def _builder(provider_id: str, **_: Any) -> Any:
        if provider_id in available:
            value = available[provider_id]
            if isinstance(value, Exception):
                raise value
            return value
        raise UnknownProviderError(f"unknown: {provider_id}")

    return _builder


# ── happy paths ───────────────────────────────────────────────────────────────


def test_resolve_returns_primary_when_available() -> None:
    sentinel = _Sentinel("claude-code")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders({"claude-code": sentinel}),
    ):
        result = resolve_adapter("claude-code", "anthropic")
    assert result is sentinel  # type: ignore[comparison-overlap]


def test_resolve_falls_back_to_declared_fallback() -> None:
    sentinel = _Sentinel("anthropic")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders(
            {
                "claude-code": MissingCliBinaryError("claude-code", "claude"),
                "anthropic": sentinel,
            }
        ),
    ):
        result = resolve_adapter("claude-code", "anthropic")
    assert result is sentinel  # type: ignore[comparison-overlap]


def test_resolve_falls_through_to_default_cascade() -> None:
    """Both declared providers unavailable → walk DEFAULT_CASCADE."""
    sentinel = _Sentinel("lm-studio")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders(
            {
                "gemini": MissingApiKeyError("no GEMINI_API_KEY"),
                "openai": MissingApiKeyError("no OPENAI_API_KEY"),
                "lm-studio": sentinel,
            }
        ),
    ):
        result = resolve_adapter("gemini", "openai")
    assert result is sentinel  # type: ignore[comparison-overlap]


def test_resolve_with_no_arguments_walks_default_cascade() -> None:
    sentinel = _Sentinel("claude-code")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders({"claude-code": sentinel}),
    ):
        result = resolve_adapter()
    assert result is sentinel  # type: ignore[comparison-overlap]


def test_resolve_deduplicates_repeated_candidates() -> None:
    """If provider == fallback_provider, get_adapter is only called once for it."""
    calls: list[str] = []

    def _track(provider_id: str, **_: Any) -> Any:
        calls.append(provider_id)
        raise MissingApiKeyError(f"no key for {provider_id}")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_track),
        pytest.raises(NoProviderAvailableError),
    ):
        resolve_adapter("anthropic", "anthropic")

    # "anthropic" appears once at the head, then the rest of DEFAULT_CASCADE.
    assert calls.count("anthropic") == 1


# ── error paths ───────────────────────────────────────────────────────────────


def test_resolve_raises_when_nothing_available() -> None:
    with (
        patch(
            "artemis.providers.resolver.get_adapter",
            side_effect=MissingApiKeyError("nothing"),
        ),
        pytest.raises(NoProviderAvailableError) as exc_info,
    ):
        resolve_adapter("anthropic", "openai")

    msg = str(exc_info.value)
    assert "anthropic" in msg
    assert "openai" in msg


def test_resolve_skips_unknown_provider_and_continues() -> None:
    sentinel = _Sentinel("anthropic")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders(
            {
                # nonsense_provider → UnknownProviderError (raised by _make_builders)
                "anthropic": sentinel,
            }
        ),
    ):
        result = resolve_adapter("nonsense_provider", "anthropic")
    assert result is sentinel  # type: ignore[comparison-overlap]


def test_resolve_swallows_unexpected_exceptions_and_keeps_walking() -> None:
    """A broken adapter constructor shouldn't bring down the whole cascade."""
    sentinel = _Sentinel("anthropic")

    def _builder(provider_id: str, **_: Any) -> Any:
        if provider_id == "claude-code":
            raise RuntimeError("totally unexpected boom")
        if provider_id == "anthropic":
            return sentinel
        raise UnknownProviderError(provider_id)

    with patch("artemis.providers.resolver.get_adapter", side_effect=_builder):
        result = resolve_adapter("claude-code", "anthropic")
    assert result is sentinel  # type: ignore[comparison-overlap]


# ── kwargs propagation ────────────────────────────────────────────────────────


def test_resolve_forwards_kwargs_to_get_adapter() -> None:
    captured: dict[str, Any] = {}
    sentinel = _Sentinel("openai")

    def _builder(provider_id: str, **kwargs: Any) -> Any:
        captured["provider"] = provider_id
        captured["kwargs"] = kwargs
        return sentinel

    with patch("artemis.providers.resolver.get_adapter", side_effect=_builder):
        resolve_adapter("openai", api_key="sk-test", default_model="gpt-4o")

    assert captured["provider"] == "openai"
    assert captured["kwargs"] == {"api_key": "sk-test", "default_model": "gpt-4o"}


# ── DEFAULT_CASCADE invariants ────────────────────────────────────────────────


def test_default_cascade_starts_with_claude_code() -> None:
    """Operator default: prefer the local CLI before any API-key-requiring provider."""
    assert DEFAULT_CASCADE[0] == "claude-code"


def test_default_cascade_contains_anthropic_as_last_resort() -> None:
    assert "anthropic" in DEFAULT_CASCADE
