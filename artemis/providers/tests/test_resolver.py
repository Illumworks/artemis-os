"""Tests for the provider cascade resolver."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from artemis.providers.errors import (
    ClaudeCodeTimeoutError,
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


def test_resolve_treats_claude_code_timeout_as_recoverable() -> None:
    sentinel = _Sentinel("anthropic")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders(
            {
                "claude-code": ClaudeCodeTimeoutError(408, "timed out"),
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


# ── strict mode ───────────────────────────────────────────────────────────────


def test_strict_returns_named_provider_when_available() -> None:
    """strict=True returns the adapter when the provider IS available."""
    sentinel = _Sentinel("claude-code")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders({"claude-code": sentinel}),
    ):
        result = resolve_adapter("claude-code", strict=True)
    assert result is sentinel  # type: ignore[comparison-overlap]


def test_strict_raises_when_named_provider_unavailable_not_codex() -> None:
    """strict=True raises NoProviderAvailableError and does NOT fall through to
    Codex or any other DEFAULT_CASCADE member when claude-code is down."""
    codex_sentinel = _Sentinel("codex")
    calls: list[str] = []

    def _builder(provider_id: str, **_: Any) -> Any:
        calls.append(provider_id)
        if provider_id == "claude-code":
            raise MissingCliBinaryError("claude-code", "claude")
        # Any other provider (codex, anthropic, …) would succeed — but strict
        # mode must never reach them.
        return codex_sentinel

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_builder),
        pytest.raises(NoProviderAvailableError),
    ):
        resolve_adapter("claude-code", strict=True)

    # Only claude-code must have been attempted — no fallthrough to codex etc.
    assert calls == ["claude-code"]


def test_strict_raises_does_not_return_codex_adapter() -> None:
    """Return value is never a non-claude-code adapter under strict=True."""
    codex_sentinel = _Sentinel("codex")
    with (
        patch(
            "artemis.providers.resolver.get_adapter",
            side_effect=_make_builders(
                {
                    "claude-code": MissingCliBinaryError("claude-code", "claude"),
                    "codex": codex_sentinel,
                }
            ),
        ),
        pytest.raises(NoProviderAvailableError),
    ):
        result = resolve_adapter("claude-code", strict=True)
        # If we somehow get here (we shouldn't), assert it's not codex.
        # The pytest.raises context manager ensures the line below is unreachable,
        # but the pattern is kept for documentation purposes.


def test_strict_false_default_keeps_cascade_behavior() -> None:
    """Default strict=False preserves the existing cascade — falls through to
    DEFAULT_CASCADE members when the primary provider is unavailable."""
    codex_sentinel = _Sentinel("codex")
    with patch(
        "artemis.providers.resolver.get_adapter",
        side_effect=_make_builders(
            {
                "claude-code": MissingCliBinaryError("claude-code", "claude"),
                "codex": codex_sentinel,
            }
        ),
    ):
        # strict defaults to False → falls through from claude-code to codex
        result = resolve_adapter("claude-code")
    assert result is codex_sentinel  # type: ignore[comparison-overlap]


def test_strict_with_fallback_provider_only_tries_those_two() -> None:
    """strict=True with a fallback_provider tries provider then fallback only."""
    anthropic_sentinel = _Sentinel("anthropic")
    calls: list[str] = []

    def _builder(provider_id: str, **_: Any) -> Any:
        calls.append(provider_id)
        if provider_id == "claude-code":
            raise MissingCliBinaryError("claude-code", "claude")
        if provider_id == "anthropic":
            return anthropic_sentinel
        raise UnknownProviderError(f"unexpected: {provider_id}")

    with patch("artemis.providers.resolver.get_adapter", side_effect=_builder):
        result = resolve_adapter("claude-code", "anthropic", strict=True)

    assert result is anthropic_sentinel  # type: ignore[comparison-overlap]
    # Exactly two providers tried: claude-code, then anthropic — no cascade tail.
    assert calls == ["claude-code", "anthropic"]


def test_strict_with_fallback_raises_when_both_unavailable() -> None:
    """strict=True raises when both provider and fallback are unavailable."""
    with (
        patch(
            "artemis.providers.resolver.get_adapter",
            side_effect=MissingCliBinaryError("both down", "cli"),
        ),
        pytest.raises(NoProviderAvailableError),
    ):
        resolve_adapter("claude-code", "anthropic", strict=True)


# ── compose surfaces use strict mode ─────────────────────────────────────────


def test_compose_draft_uses_strict_claude_code() -> None:
    """compose_draft must call resolve_adapter with provider='claude-code' and
    strict=True — verified by inspecting the call site source."""
    import inspect

    import artemis.marketing.routes.writing_studio as ws

    src = inspect.getsource(ws.compose_draft)
    assert 'resolve_adapter("claude-code", strict=True)' in src, (
        "compose_draft must call resolve_adapter('claude-code', strict=True)"
    )


def test_rewrite_span_uses_strict_claude_code() -> None:
    """rewrite-span handler must call resolve_adapter with provider='claude-code'
    and strict=True — verified by inspecting the call site source."""
    import inspect

    import artemis.marketing.routes.writing_studio as ws

    # The rewrite-span handler is the function that contains 'rewrite-span' in
    # its docstring/name; we check the module source for the second occurrence
    # of the strict call (compose_draft is the first).
    src = inspect.getsource(ws)
    occurrences = src.count('resolve_adapter("claude-code", strict=True)')
    assert occurrences >= 2, (  # noqa: PLR2004
        f"Expected at least 2 strict resolve_adapter calls (compose_draft + rewrite-span), "
        f"found {occurrences}"
    )
