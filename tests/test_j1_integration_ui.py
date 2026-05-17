"""Phase J1 — Integration UI route tests.

Tests:
  1.  test_list_integrations_empty          — GET /api/integrations when DB is empty → 200, []
  2.  test_list_integrations_with_filter    — GET /api/integrations?provider=slack → 200, list
  3.  test_revoke_integration_not_found     — DELETE /api/integrations/99999 → 404
  4.  test_slack_oauth_start_without_creds  — GET /api/integrations/slack/oauth/start, no SLACK_CLIENT_ID → 503
  5.  test_slack_oauth_start_with_creds     — GET /api/integrations/slack/oauth/start, SLACK_CLIENT_ID=test → 200, url contains slack.com
  6.  test_slack_oauth_callback_invalid_state — GET callback with state=invalid → 400
  7.  test_slack_verify_no_integration      — GET /api/integrations/slack/verify with empty DB → 404
  8.  test_list_integrations_returns_correct_schema — mock a connected integration, verify response shape
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
import artemis.integrations.models  # noqa: F401 — register models on Base.metadata
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB setup ─────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)

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
    "TRUNCATE integration_configs, integrations, slack_inbound_messages RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session — integrations table truncated before each test."""
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
    """HTTP client bound to the FastAPI app via ASGI transport."""
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_list_integrations_empty(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/integrations with an empty table returns 200 and an empty list."""
    response = await client.get("/api/integrations")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert data == []


async def test_list_integrations_with_filter(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/integrations?provider=slack returns 200 (may be empty, but no error)."""
    response = await client.get("/api/integrations", params={"provider": "slack"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # All returned rows must be for the requested provider
    for row in data:
        assert row["provider"] == "slack"


async def test_revoke_integration_not_found(client: AsyncClient, db_session: AsyncSession) -> None:
    """DELETE /api/integrations/99999 returns 404 when the row does not exist."""
    response = await client.delete("/api/integrations/99999")
    assert response.status_code == 404


async def test_slack_oauth_start_without_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /api/integrations/slack/oauth/start with no SLACK_CLIENT_ID → 503."""
    with patch.dict(os.environ, {}, clear=False):
        # Ensure neither var is set
        os.environ.pop("SLACK_CLIENT_ID", None)
        os.environ.pop("SLACK_CLIENT_SECRET", None)
        response = await client.get("/api/integrations/slack/oauth/start")

    assert response.status_code == 503


async def test_slack_oauth_start_with_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /api/integrations/slack/oauth/start with all creds set → 200, url contains slack.com."""
    env_patch = {
        "SLACK_CLIENT_ID": "test_client_id",
        "SLACK_CLIENT_SECRET": "test_client_secret",
        "SLACK_SIGNING_SECRET": "test_signing_secret",
    }
    with patch.dict(os.environ, env_patch):
        response = await client.get("/api/integrations/slack/oauth/start")

    assert response.status_code == 200
    body = response.json()
    assert "url" in body
    assert "slack.com" in body["url"]


async def test_slack_oauth_callback_invalid_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """GET /api/integrations/slack/oauth/callback with an unknown state → 400."""
    env_patch = {
        "SLACK_CLIENT_ID": "test_client_id",
        "SLACK_CLIENT_SECRET": "test_client_secret",
    }
    with patch.dict(os.environ, env_patch):
        response = await client.get(
            "/api/integrations/slack/oauth/callback",
            params={"code": "some_code", "state": "invalid_state_xyz"},
        )

    assert response.status_code == 400


async def test_slack_verify_no_integration(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /api/integrations/slack/verify with empty DB → 404."""
    env_patch = {
        "SLACK_CLIENT_ID": "test_client_id",
        "SLACK_CLIENT_SECRET": "test_client_secret",
    }
    with patch.dict(os.environ, env_patch):
        response = await client.get("/api/integrations/slack/verify")

    assert response.status_code == 404


async def test_list_integrations_returns_correct_schema(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Mocked connected integration → response matches IntegrationOut schema shape."""
    from datetime import UTC, datetime

    from artemis.integrations.models import Integration

    # Insert a minimal integration row directly
    row = Integration(
        provider="slack",
        workspace_id="T_TEST_WORKSPACE",
        display_name="Acme Workspace",
        encrypted_credentials=b"stub",
        status="active",
        connected_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.commit()

    response = await client.get("/api/integrations")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    entry = next((r for r in data if r["provider"] == "slack"), None)
    assert entry is not None, "Expected at least one slack integration in the response"

    # Verify required IntegrationOut fields are present
    assert "id" in entry
    assert "provider" in entry
    assert "workspace_id" in entry
    assert "connected_at" in entry
    assert "status" in entry
    assert entry["provider"] == "slack"
    assert entry["workspace_id"] == "T_TEST_WORKSPACE"
    assert entry["status"] == "active"
