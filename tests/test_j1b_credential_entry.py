"""Phase J1b — Credential entry + provider config round-trip tests.

Tests:
  1.  test_unknown_provider_get_config         — GET /providers/unknown/config → 404
  2.  test_unknown_provider_post_config        — POST /providers/unknown/config → 404
  3.  test_unknown_provider_delete_config      — DELETE /providers/unknown/config → 404
  4.  test_get_config_before_any_save          — GET /providers/slack/config when empty → ever_configured=False
  5.  test_post_all_three_fields               — POST 3 fields → all configured_keys True, ever_configured=True
  6.  test_post_partial_update_keeps_others    — POST 1 field after full set → others remain True
  7.  test_post_empty_body_rejected            — POST {} → 422
  8.  test_delete_clears_config               — DELETE → GET returns ever_configured=False
  9.  test_resolve_slack_config_db_wins        — DB creds override env vars per-field
  10. test_resolve_slack_config_env_fallback   — missing DB fields fall back to env
  11. test_resolve_slack_config_per_field_mix  — DB wins per-field (some DB, some env)
  12. test_resolve_slack_config_missing_raises — no DB + no env → MissingProviderConfigError
  13. test_oauth_start_uses_db_client_id       — OAuth start picks up DB client_id when set
  14. test_events_receiver_uses_db_signing_secret — events endpoint uses DB signing_secret
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db as db_module
import artemis.integrations.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB ───────────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    "TRUNCATE integration_configs, integrations, slack_inbound_messages RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Helpers ───────────────────────────────────────────────────────────────────


def _slack_signature(body: bytes, secret: str, ts: int | None = None) -> tuple[str, str]:
    if ts is None:
        ts = int(time.time())
    sig_base = f"v0:{ts}:{body.decode()}"
    mac = hmac.new(secret.encode(), sig_base.encode(), hashlib.sha256)
    return str(ts), f"v0={mac.hexdigest()}"


# ── Unknown provider guard ────────────────────────────────────────────────────


async def test_unknown_provider_get_config(client: AsyncClient, db_session: AsyncSession) -> None:
    r = await client.get("/api/integrations/providers/notreal/config")
    assert r.status_code == 404


async def test_unknown_provider_post_config(client: AsyncClient, db_session: AsyncSession) -> None:
    r = await client.post(
        "/api/integrations/providers/notreal/config",
        json={"some_key": "val"},
    )
    assert r.status_code == 404


async def test_unknown_provider_delete_config(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    r = await client.delete("/api/integrations/providers/notreal/config")
    assert r.status_code == 404


# ── GET before any save ───────────────────────────────────────────────────────


async def test_get_config_before_any_save(client: AsyncClient, db_session: AsyncSession) -> None:
    r = await client.get("/api/integrations/providers/slack/config")
    assert r.status_code == 200
    body = r.json()
    assert body["ever_configured"] is False
    assert body["configured_keys"] == {}


# ── POST all three fields ─────────────────────────────────────────────────────


async def test_post_all_three_fields(client: AsyncClient, db_session: AsyncSession) -> None:
    payload = {
        "client_id": "C123",
        "client_secret": "S456",
        "signing_secret": "X789",
    }
    r = await client.post("/api/integrations/providers/slack/config", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["ever_configured"] is True
    assert body["configured_keys"]["client_id"] is True
    assert body["configured_keys"]["client_secret"] is True
    assert body["configured_keys"]["signing_secret"] is True

    # Verify GET reflects the same state
    r2 = await client.get("/api/integrations/providers/slack/config")
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["ever_configured"] is True
    assert all(b2["configured_keys"].get(k) is True for k in payload)


# ── Partial update keeps existing fields ──────────────────────────────────────


async def test_post_partial_update_keeps_others(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # First: set all three
    await client.post(
        "/api/integrations/providers/slack/config",
        json={"client_id": "C_OLD", "client_secret": "S_OLD", "signing_secret": "X_OLD"},
    )

    # Then: update only client_id
    r = await client.post(
        "/api/integrations/providers/slack/config",
        json={"client_id": "C_NEW"},
    )
    assert r.status_code == 200
    body = r.json()
    # All three keys still reported as set
    assert body["configured_keys"]["client_id"] is True
    assert body["configured_keys"]["client_secret"] is True
    assert body["configured_keys"]["signing_secret"] is True


# ── Empty body rejected ───────────────────────────────────────────────────────


async def test_post_empty_body_rejected(client: AsyncClient, db_session: AsyncSession) -> None:
    r = await client.post("/api/integrations/providers/slack/config", json={})
    assert r.status_code == 422


# ── DELETE clears config ──────────────────────────────────────────────────────


async def test_delete_clears_config(client: AsyncClient, db_session: AsyncSession) -> None:
    # Set creds
    await client.post(
        "/api/integrations/providers/slack/config",
        json={"client_id": "C1", "client_secret": "S1", "signing_secret": "X1"},
    )
    # Delete
    r_del = await client.delete("/api/integrations/providers/slack/config")
    assert r_del.status_code == 204

    # GET now reports not configured
    r = await client.get("/api/integrations/providers/slack/config")
    body = r.json()
    assert body["ever_configured"] is False
    assert body["configured_keys"] == {}


# ── resolve_slack_config unit tests ──────────────────────────────────────────


async def test_resolve_slack_config_db_wins(db_session: AsyncSession) -> None:
    """DB credentials override env vars when present."""
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_slack_config

    await repo.upsert_provider_config(
        db_session,
        "slack",
        {"client_id": "DB_CLIENT_ID", "client_secret": "DB_SECRET", "signing_secret": "DB_SIGN"},
    )
    await db_session.commit()

    env = {
        "SLACK_CLIENT_ID": "ENV_CLIENT_ID",
        "SLACK_CLIENT_SECRET": "ENV_SECRET",
        "SLACK_SIGNING_SECRET": "ENV_SIGN",
    }
    with patch.dict(os.environ, env):
        cfg = await resolve_slack_config(db_session)

    assert cfg.client_id == "DB_CLIENT_ID"
    assert cfg.client_secret == "DB_SECRET"
    assert cfg.signing_secret == "DB_SIGN"


async def test_resolve_slack_config_env_fallback(db_session: AsyncSession) -> None:
    """When no DB row exists, env vars fill all fields."""
    from artemis.integrations.config_resolver import resolve_slack_config

    env = {
        "SLACK_CLIENT_ID": "ENV_CID",
        "SLACK_CLIENT_SECRET": "ENV_CSECRET",
        "SLACK_SIGNING_SECRET": "ENV_SSECRET",
    }
    with patch.dict(os.environ, env):
        cfg = await resolve_slack_config(db_session)

    assert cfg.client_id == "ENV_CID"
    assert cfg.client_secret == "ENV_CSECRET"
    assert cfg.signing_secret == "ENV_SSECRET"


async def test_resolve_slack_config_per_field_mix(db_session: AsyncSession) -> None:
    """DB fills client_id; env fills missing fields."""
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_slack_config

    # Only client_id in DB
    await repo.upsert_provider_config(db_session, "slack", {"client_id": "DB_CID"})
    await db_session.commit()

    env = {
        "SLACK_CLIENT_ID": "ENV_CID",  # DB wins — should be ignored
        "SLACK_CLIENT_SECRET": "ENV_CSECRET",
        "SLACK_SIGNING_SECRET": "ENV_SSECRET",
    }
    with patch.dict(os.environ, env):
        cfg = await resolve_slack_config(db_session)

    assert cfg.client_id == "DB_CID"
    assert cfg.client_secret == "ENV_CSECRET"
    assert cfg.signing_secret == "ENV_SSECRET"


async def test_resolve_slack_config_missing_raises(db_session: AsyncSession) -> None:
    """All fields absent (no DB, no env) → MissingProviderConfigError."""
    from artemis.integrations.config_resolver import (
        MissingProviderConfigError,
        resolve_slack_config,
    )

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SLACK_CLIENT_ID", None)
        os.environ.pop("SLACK_CLIENT_SECRET", None)
        os.environ.pop("SLACK_SIGNING_SECRET", None)

        with pytest.raises(MissingProviderConfigError) as exc_info:
            await resolve_slack_config(db_session)

    err = exc_info.value
    assert "client_id" in err.missing_fields or "client_secret" in err.missing_fields


# ── OAuth start uses DB client_id ─────────────────────────────────────────────


async def test_oauth_start_uses_db_client_id(client: AsyncClient, db_session: AsyncSession) -> None:
    """OAuth /start picks up client_id from DB rather than env when set."""
    from artemis.integrations import repository as repo

    await repo.upsert_provider_config(
        db_session,
        "slack",
        {
            "client_id": "DB_OAUTH_CID",
            "client_secret": "DB_OAUTH_SECRET",
            "signing_secret": "DB_SIGN",
        },
    )
    await db_session.commit()

    # Env has different (decoy) client_id
    with patch.dict(os.environ, {"SLACK_CLIENT_ID": "ENV_DECOY"}):
        r = await client.get("/api/integrations/slack/oauth/start")

    assert r.status_code == 200
    url = r.json()["url"]
    assert "DB_OAUTH_CID" in url
    assert "ENV_DECOY" not in url


# ── Events receiver uses DB signing_secret ────────────────────────────────────


async def test_events_receiver_uses_db_signing_secret(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Slack events endpoint verifies HMAC with signing_secret from DB."""
    from artemis.integrations import repository as repo

    db_secret = "db_signing_secret_xyz"
    await repo.upsert_provider_config(
        db_session,
        "slack",
        {"client_id": "C1", "client_secret": "S1", "signing_secret": db_secret},
    )
    await db_session.commit()

    event_body = json.dumps(
        {
            "type": "event_callback",
            "event_id": "Ev_DB_SECRET",
            "team_id": "T1",
            "event": {
                "type": "app_mention",
                "channel": "C_GEN",
                "user": "U1",
                "text": "hi",
                "ts": "1.0",
            },
        }
    ).encode()
    ts, sig = _slack_signature(event_body, db_secret)

    # Mock handle_turn at its source module (lazy-imported inside route_inbound).
    # Return response_text=None so route_inbound exits before the Slack reply step.
    _turn_result = MagicMock()
    _turn_result.response_text = None
    with (
        patch(
            "artemis.floating_artemis.chat.handle_turn",
            new=AsyncMock(return_value=_turn_result),
        ),
        patch.dict(os.environ, {"SLACK_SIGNING_SECRET": "wrong_env_secret"}),
    ):
        r = await client.post(
            "/api/integrations/slack/events",
            content=event_body,
            headers={
                "Content-Type": "application/json",
                "X-Slack-Request-Timestamp": ts,
                "X-Slack-Signature": sig,
            },
        )

    # 200 means the DB secret was used (env secret would have caused 401)
    assert r.status_code == 200
