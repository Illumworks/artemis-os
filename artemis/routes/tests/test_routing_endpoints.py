"""Tests for routing control surface endpoints (Part D + Part H of brief).

Test numbers match the brief (tests 6-15):
  6.  GET /api/routing/health returns all providers.
  7.  GET /api/routing/features returns full feature catalog with current cascades.
  8.  POST /api/routing/features/memory_consolidation/override with valid cascade → 200.
  9.  POST same feature twice → row updated, not duplicated.
  10. POST with unknown feature_tag → 422.
  11. POST with empty cascade → 422.
  12. POST with unknown provider in cascade → 422.
  13. POST with unavailable provider in cascade → 200 with warning in response.
  14. DELETE /api/routing/features/memory_consolidation/override → active=false, log entry.
  15. GET /api/routing/changes-log returns audit rows newest first.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db
from artemis.db import attach_pgvector_codec

# Guard against wrong DB
_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not the test database.")

_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text(
    "TRUNCATE feature_routing_overrides, routing_changes_log, app_settings RESTART IDENTITY CASCADE"
)

# Import routing models so they are registered on Base.metadata for alembic
import artemis.providers.routing_models  # noqa: F401, E402


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
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Fake health for test isolation ────────────────────────────────────────────


_FAKE_HEALTH_UNAVAILABLE = [
    {
        "provider": p,
        "available": False,
        "latency_ms": None,
        "version": None,
        "error": "test env",
        "checked_at": "2026-06-06T00:00:00Z",
        "models": None,
    }
    for p in ["claude-code", "codex", "lm-studio", "anthropic", "openai", "gemini", "openrouter"]
]


async def _fake_health_all_unavailable():
    """Async mock returning all providers as unavailable for test isolation."""
    return _FAKE_HEALTH_UNAVAILABLE


# ── Test 6: GET /api/routing/health ──────────────────────────────────────────


async def test_get_health_returns_all_providers(client: AsyncClient) -> None:
    """GET /api/routing/health returns records for all known providers."""
    with patch(
        "artemis.routes.routing.probe_all_providers", side_effect=_fake_health_all_unavailable
    ):
        resp = await client.get("/api/routing/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    provider_ids = {p["provider"] for p in data["providers"]}
    for expected in [
        "claude-code",
        "codex",
        "lm-studio",
        "anthropic",
        "openai",
        "gemini",
        "openrouter",
    ]:
        assert expected in provider_ids


# ── Test 7: GET /api/routing/features ────────────────────────────────────────


async def test_get_features_returns_full_catalog(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /api/routing/features returns all feature_tags with cascades."""
    from artemis.providers.feature_catalog import FEATURE_TAGS

    resp = await client.get("/api/routing/features")
    assert resp.status_code == 200
    data = resp.json()
    assert "features" in data
    tags_returned = {f["feature_tag"] for f in data["features"]}
    for tag in FEATURE_TAGS:
        assert tag in tags_returned
    # All features with no override should have is_override=False
    for f in data["features"]:
        assert "current_cascade" in f
        assert "default_cascade" in f
        assert "is_override" in f


# ── Test 8: POST override valid ───────────────────────────────────────────────


async def test_post_feature_override_valid(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /api/routing/features/memory_consolidation/override with valid cascade → 200."""
    with patch(
        "artemis.routes.routing.probe_all_providers", side_effect=_fake_health_all_unavailable
    ):
        resp = await client.post(
            "/api/routing/features/memory_consolidation/override",
            json={
                "cascade": [
                    {"provider": "lm-studio", "model": "qwen/qwen3-14b"},
                    {"provider": "claude-code"},
                ],
                "reason": "test override",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["feature_tag"] == "memory_consolidation"
    assert data["active"] is True
    assert len(data["cascade"]) == 2
    assert data["cascade"][0]["provider"] == "lm-studio"


# ── Test 9: POST same feature twice → update not duplicate ────────────────────


async def test_post_feature_override_upsert(client: AsyncClient, db_session: AsyncSession) -> None:
    """Posting override twice updates the row, does not create a duplicate."""
    from sqlalchemy import select

    from artemis.providers.routing_models import FeatureRoutingOverride

    with patch(
        "artemis.routes.routing.probe_all_providers", side_effect=_fake_health_all_unavailable
    ):
        await client.post(
            "/api/routing/features/trajectory_summary/override",
            json={"cascade": [{"provider": "lm-studio"}], "reason": "first"},
        )
        await client.post(
            "/api/routing/features/trajectory_summary/override",
            json={"cascade": [{"provider": "claude-code"}], "reason": "second"},
        )

    result = await db_session.execute(
        select(FeatureRoutingOverride).where(
            FeatureRoutingOverride.feature_tag == "trajectory_summary"
        )
    )
    rows = result.scalars().all()
    assert len(rows) == 1, "Should have exactly one row after two POSTs"
    assert rows[0].cascade[0]["provider"] == "claude-code"  # updated to second


# ── Test 10: POST unknown feature_tag → 422 ───────────────────────────────────


async def test_post_unknown_feature_tag_returns_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/routing/features/nonexistent_feature_xyz/override",
        json={"cascade": [{"provider": "claude-code"}], "reason": "test"},
    )
    assert resp.status_code == 422


# ── Test 11: POST empty cascade → 422 ────────────────────────────────────────


async def test_post_empty_cascade_returns_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/routing/features/memory_consolidation/override",
        json={"cascade": [], "reason": "test"},
    )
    assert resp.status_code == 422


# ── Test 12: POST unknown provider in cascade → 422 ──────────────────────────


async def test_post_unknown_provider_returns_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/routing/features/memory_consolidation/override",
        json={"cascade": [{"provider": "definitely-not-a-real-provider"}], "reason": "test"},
    )
    assert resp.status_code == 422


# ── Test 13: POST unavailable provider → 200 with warnings ───────────────────


async def test_post_unavailable_provider_warns_but_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """An unavailable provider in the cascade → 200 with warnings."""
    with patch(
        "artemis.routes.routing.probe_all_providers", side_effect=_fake_health_all_unavailable
    ):
        resp = await client.post(
            "/api/routing/features/meeting_summary/override",
            json={"cascade": [{"provider": "gemini"}], "reason": "staging for future key"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["active"] is True
    # gemini is unavailable in fake health → warnings present
    assert isinstance(data["warnings"], list)
    assert len(data["warnings"]) > 0
    assert any("gemini" in w for w in data["warnings"])


# ── Test 14: DELETE override → active=False + log entry ──────────────────────


async def test_delete_feature_override(client: AsyncClient, db_session: AsyncSession) -> None:
    """DELETE /api/routing/features/memory_consolidation/override → active=False."""
    from sqlalchemy import select

    from artemis.providers.routing_models import FeatureRoutingOverride, RoutingChangeLog

    # First create an override
    with patch(
        "artemis.routes.routing.probe_all_providers", side_effect=_fake_health_all_unavailable
    ):
        create_resp = await client.post(
            "/api/routing/features/memory_consolidation/override",
            json={"cascade": [{"provider": "lm-studio"}], "reason": "to be deleted"},
        )
    assert create_resp.status_code == 200

    # Now delete it
    del_resp = await client.delete("/api/routing/features/memory_consolidation/override")
    assert del_resp.status_code == 200
    data = del_resp.json()
    assert data["active"] is False

    # Verify row is active=False (not hard-deleted)
    result = await db_session.execute(
        select(FeatureRoutingOverride).where(
            FeatureRoutingOverride.feature_tag == "memory_consolidation"
        )
    )
    row = result.scalar_one_or_none()
    assert row is not None, "Row must still exist (lossless)"
    assert row.active is False

    # Verify log entry exists
    log_result = await db_session.execute(
        select(RoutingChangeLog).where(RoutingChangeLog.scope_value == "memory_consolidation")
    )
    log_rows = log_result.scalars().all()
    assert len(log_rows) >= 2  # create + delete


# ── Test 15: GET /api/routing/changes-log newest first ───────────────────────


async def test_get_changes_log_newest_first(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/routing/changes-log returns changes newest-first."""
    with patch(
        "artemis.routes.routing.probe_all_providers", side_effect=_fake_health_all_unavailable
    ):
        await client.post(
            "/api/routing/features/trajectory_summary/override",
            json={"cascade": [{"provider": "lm-studio"}], "reason": "first change"},
        )
        await client.post(
            "/api/routing/features/memory_consolidation/override",
            json={"cascade": [{"provider": "gemini"}], "reason": "second change"},
        )

    resp = await client.get("/api/routing/changes-log?limit=10&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert "changes" in data
    assert len(data["changes"]) >= 2
    # Verify newest-first ordering
    timestamps = [c["changed_at"] for c in data["changes"]]
    assert timestamps == sorted(timestamps, reverse=True)


# ── Default cascade endpoints ────────────────────────────────────────────────


async def test_get_default_cascade(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/routing/default-cascade returns current cascade."""
    resp = await client.get("/api/routing/default-cascade")
    assert resp.status_code == 200
    data = resp.json()
    assert "cascade" in data
    assert isinstance(data["cascade"], list)
    assert len(data["cascade"]) > 0


async def test_post_default_cascade(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST /api/routing/default-cascade updates and logs the change."""
    resp = await client.post(
        "/api/routing/default-cascade",
        json={"cascade": ["lm-studio", "claude-code"], "reason": "test cascade change"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["cascade"] == ["lm-studio", "claude-code"]

    # Verify it's persisted
    get_resp = await client.get("/api/routing/default-cascade")
    assert get_resp.status_code == 200
    get_data = get_resp.json()
    assert get_data["cascade"] == ["lm-studio", "claude-code"]
    assert get_data["source"] == "persisted"
