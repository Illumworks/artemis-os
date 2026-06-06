"""Tests for the Apply button flow — Phase 3 integration tests.

Tests 9-10 from the brief:
  9.  Apply button POST hits routing-control-surface endpoint with correct body.
  10. Apply with unavailable provider in cascade returns 422 from backend.

These tests verify that the Apply flow in the Cost tab correctly delegates
to POST /api/routing/features/{tag}/override (Phase R foundation endpoint).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.costs.models  # noqa: F401
import artemis.db as db_module
import artemis.providers.routing_models  # noqa: F401
from artemis.db import attach_pgvector_codec

# ── DB guard ──────────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE feature_routing_overrides, routing_changes_log, app_settings RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE_SQL)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Test 9: Apply POST constructs correct override body ───────────────────────


async def test_apply_post_creates_override_row(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """POST /api/routing/features/{tag}/override with a valid cascade persists the override."""
    cascade = [
        {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
        {"provider": "claude-code", "model": "claude-haiku-4-5-20251001"},
    ]
    resp = await client.post(
        "/api/routing/features/trajectory_summary/override",
        json={"cascade": cascade, "reason": "cost phase 3 apply test"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["feature_tag"] == "trajectory_summary"
    assert body["cascade"] == cascade

    # Verify the row is actually in the DB
    row = (
        await db_session.execute(
            text(
                "SELECT feature_tag, active FROM feature_routing_overrides"
                " WHERE feature_tag = 'trajectory_summary'"
            )
        )
    ).one_or_none()
    assert row is not None
    assert row[1] is True  # active


# ── Test 10: Apply with unknown provider returns 422 ─────────────────────────


async def test_apply_with_unknown_provider_returns_422(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """POST with an unknown provider in the cascade is rejected with 422."""
    cascade = [{"provider": "not-a-real-provider", "model": "fake-model"}]
    resp = await client.post(
        "/api/routing/features/trajectory_summary/override",
        json={"cascade": cascade, "reason": "should be rejected"},
    )
    assert resp.status_code == 422, resp.text


# ── Test 10b: Apply with empty cascade returns 422 ────────────────────────────


async def test_apply_with_empty_cascade_returns_422(
    db_session: AsyncSession, client: AsyncClient
) -> None:
    """POST with empty cascade is rejected."""
    resp = await client.post(
        "/api/routing/features/trajectory_summary/override",
        json={"cascade": [], "reason": "empty cascade"},
    )
    assert resp.status_code == 422, resp.text
