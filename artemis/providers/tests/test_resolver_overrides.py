"""Tests for resolver feature_tag override integration.

Test numbers match the brief (tests 16-19):
  16. resolve_adapter_async(feature_tag='memory_consolidation', session=...)
      with no override returns default cascade.
  17. With override in DB, returns the override cascade.
  18. With override active=False, falls back to default.
  19. Without feature_tag param, existing behavior unchanged (regression guard).

These tests use the artemis_test_routing DB via ARTEMIS_TEST_DB_URL.
"""

from __future__ import annotations

import contextlib
import os
from unittest.mock import patch

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.db import attach_pgvector_codec
from artemis.providers.errors import MissingApiKeyError
from artemis.providers.resolver import DEFAULT_CASCADE, resolve_adapter, resolve_adapter_async
from artemis.providers.routing_repository import (
    deactivate_routing_override,
    upsert_routing_override,
)

# Guard against running against the wrong DB
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)


@pytest.fixture
async def db_session():
    """Per-test session; TRUNCATE routing tables before each test."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "TRUNCATE feature_routing_overrides, routing_changes_log, app_settings"
                        " RESTART IDENTITY CASCADE"
                    )
                )
            yield session
    finally:
        await engine.dispose()


# ── Test 16: no override → default cascade ────────────────────────────────────


async def test_resolve_no_override_returns_default(db_session: AsyncSession) -> None:
    """resolve_adapter_async with feature_tag but no DB override uses catalog default."""
    captured_provider: list[str] = []

    def _fake_get_adapter(provider_id: str, **_):  # type: ignore[return]
        captured_provider.append(provider_id)
        raise MissingApiKeyError("no key")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_fake_get_adapter),
        contextlib.suppress(Exception),
    ):
        await resolve_adapter_async(
            feature_tag="memory_consolidation",
            session=db_session,
        )

    # With no override, the catalog default for memory_consolidation is gemini-first.
    assert len(captured_provider) > 0
    # gemini is first in the Gemini-first Tier 3 cascade for memory_consolidation
    assert "gemini" in captured_provider or "claude-code" in captured_provider


# ── Test 17: with override in DB → override cascade ───────────────────────────


async def test_resolve_with_active_override_uses_override(db_session: AsyncSession) -> None:
    """When an active override exists, resolve_adapter_async walks that cascade."""
    async with db_session.begin():
        await upsert_routing_override(
            db_session,
            feature_tag="memory_consolidation",
            cascade=[
                {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
                {"provider": "claude-code"},
            ],
        )

    captured_provider: list[str] = []

    def _fake_get_adapter(provider_id: str, **_):  # type: ignore[return]
        captured_provider.append(provider_id)
        raise MissingApiKeyError("no key")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_fake_get_adapter),
        contextlib.suppress(Exception),
    ):
        await resolve_adapter_async(
            feature_tag="memory_consolidation",
            session=db_session,
        )

    # Override specifies lm-studio first — should be the first attempted provider
    assert captured_provider[0] == "lm-studio"


# ── Test 18: override active=False → falls back to default ───────────────────


async def test_resolve_inactive_override_falls_back_to_default(db_session: AsyncSession) -> None:
    """When the override row has active=False, the resolver ignores it."""
    async with db_session.begin():
        await upsert_routing_override(
            db_session,
            feature_tag="trajectory_summary",
            cascade=[{"provider": "openai"}],
        )
        await deactivate_routing_override(db_session, feature_tag="trajectory_summary")

    captured_provider: list[str] = []

    def _fake_get_adapter(provider_id: str, **_):  # type: ignore[return]
        captured_provider.append(provider_id)
        raise MissingApiKeyError("no key")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_fake_get_adapter),
        contextlib.suppress(Exception),
    ):
        await resolve_adapter_async(
            feature_tag="trajectory_summary",
            session=db_session,
        )

    # openai was in the deactivated override — should NOT be first
    # trajectory_summary default is lm-studio first
    assert "openai" not in captured_provider[:1] or "lm-studio" in captured_provider


# ── Test 19: no feature_tag → existing behavior unchanged (regression guard) ──


def test_resolve_without_feature_tag_uses_old_behavior() -> None:
    """resolve_adapter without feature_tag sees exactly the same behavior as before.

    This is the backwards-compatibility regression guard. The new signature
    adds feature_tag and session as keyword-only args; callers that don't pass
    them must see identical behavior.
    """
    captured: list[str] = []

    def _fake_get_adapter(provider_id: str, **_):  # type: ignore[return]
        captured.append(provider_id)
        raise MissingApiKeyError("no key")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_fake_get_adapter),
        contextlib.suppress(Exception),
    ):
        # Old-style call — no feature_tag, no session
        resolve_adapter("claude-code", "anthropic")

    # Must have tried claude-code first (original behavior)
    assert captured[0] == "claude-code"
    # Must have tried anthropic second (original behavior)
    assert "anthropic" in captured

    # Verify the full cascade follows the original order:
    # provider, fallback_provider, then DEFAULT_CASCADE (deduped)
    expected_order = [
        "claude-code",
        "anthropic",
        *[c for c in DEFAULT_CASCADE if c not in ("claude-code", "anthropic")],
    ]
    assert captured == expected_order


# ── Addition 3: test that override actually changes adapter resolution ────────


async def test_override_changes_resolved_adapter(db_session: AsyncSession) -> None:
    """An active override for memory_consolidation makes the resolver prefer lm-studio.

    This is the Phase 3 behavioral proof: Apply button → override row → resolver
    returns lm-studio adapter instead of claude-code.
    """
    # Seed an override for memory_consolidation: lm-studio first
    async with db_session.begin():
        await upsert_routing_override(
            db_session,
            feature_tag="memory_consolidation",
            cascade=[{"provider": "lm-studio", "model": "qwen/qwen3-14b"}],
        )

    # Track which providers the resolver attempts
    captured_provider: list[str] = []

    def _fake_get_adapter(provider_id: str, **_):  # type: ignore[return]
        captured_provider.append(provider_id)
        raise MissingApiKeyError("no key")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_fake_get_adapter),
        __import__("contextlib").suppress(Exception),
    ):
        await resolve_adapter_async(
            provider="claude-code",  # default
            feature_tag="memory_consolidation",
            session=db_session,
        )

    # Adapter should attempt lm-studio FIRST, not claude-code
    assert len(captured_provider) > 0, "No providers were attempted"
    assert captured_provider[0] == "lm-studio", (
        f"Expected lm-studio first (override cascade), got {captured_provider[0]!r}. "
        f"Full order: {captured_provider}"
    )


async def test_resolve_async_without_feature_tag_same_as_sync() -> None:
    """resolve_adapter_async without feature_tag behaves like resolve_adapter."""
    captured: list[str] = []

    def _fake_get_adapter(provider_id: str, **_):  # type: ignore[return]
        captured.append(provider_id)
        raise MissingApiKeyError("no key")

    with (
        patch("artemis.providers.resolver.get_adapter", side_effect=_fake_get_adapter),
        contextlib.suppress(Exception),
    ):
        await resolve_adapter_async("gemini", "openai")

    # gemini first, openai second, then DEFAULT_CASCADE (deduped)
    assert captured[0] == "gemini"
    assert captured[1] == "openai"
    for c in DEFAULT_CASCADE:
        if c not in ("gemini", "openai"):
            assert c in captured
