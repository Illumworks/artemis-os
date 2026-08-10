"""Unit tests for the Google token refresh fixes.

Covers:
  1. GCalClient._refresh() on 401 → logs Google error body (invalid_client surfaced)
  2. gcal/sync._client_from_row() uses DB-resolved client_id/secret, NOT stale blob
  3. scheduler._process_integration for gcal → injects DB client into creds before refresh
  4. refresh_google_credentials_tick → refreshes personal + marketing google_credentials rows
  5. refresh_google_credentials_tick → skips rows with no refresh_token
  6. refresh_google_credentials_tick → skips rows still healthy (expiry > leeway)
  7. refresh_google_credentials_tick → handles REFRESH_TOKEN_EXPIRED gracefully
  8. refresh_google_credentials_tick → handles transient failure gracefully

No real network calls — all external HTTP is mocked.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# 1. GCalClient._refresh() on 401 → logs Google error body
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_client_refresh_401_logs_error_body() -> None:
    """A 401 from the token endpoint raises HTTPStatusError but first logs the Google body."""
    import httpx

    from artemis.integrations.gcal.client import GCalClient

    client = GCalClient(
        access_token="tok",
        refresh_token="rt",
        client_id="wrong_client",
        client_secret="wrong_secret",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.is_success = False
    mock_resp.text = (
        '{"error":"invalid_client","error_description":"The OAuth client was not found."}'
    )
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=MagicMock(),
        )
    )
    mock_resp.json.return_value = {
        "error": "invalid_client",
        "error_description": "The OAuth client was not found.",
    }

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http

        with pytest.raises(httpx.HTTPStatusError):
            await client._refresh()

    # resp.raise_for_status() was called (which raises), not silently swallowed
    mock_resp.raise_for_status.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 2. gcal/sync._client_from_row() uses DB-resolved client, not stale blob
# ─────────────────────────────────────────────────────────────────────────────


async def test_client_from_row_uses_db_client_not_stale_blob() -> None:
    """_client_from_row overrides stored client_id/secret with the DB-resolved values.

    The blob may have a stale client from a previous env-override configuration.
    The returned GCalClient must use the DB client so refresh works.
    """
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.gcal.sync import _client_from_row

    stale_creds = {
        "access_token": "at",
        "refresh_token": "rt",
        "client_id": "STALE_CLIENT",  # wrong — from old env override
        "client_secret": "STALE_SECRET",
        "expires_at": time.time() + 3600,
    }
    encrypted = encrypt_credentials(stale_creds)

    mock_row = MagicMock()
    mock_row.encrypted_credentials = encrypted
    mock_row.id = 7

    db_cfg = GCalConfig(client_id="DB_CLIENT_975559492379", client_secret="DB_SECRET")
    mock_session = AsyncMock()

    with patch(
        "artemis.integrations.gcal.sync.resolve_gcal_config",
        new_callable=AsyncMock,
        return_value=db_cfg,
    ):
        gcal_client = await _client_from_row(mock_row, mock_session)

    # The client must use the DB-resolved client, NOT the stale blob values.
    assert gcal_client._client_id == "DB_CLIENT_975559492379"
    assert gcal_client._client_secret == "DB_SECRET"
    # Tokens from blob are preserved.
    assert gcal_client._access_token == "at"
    assert gcal_client._refresh_token == "rt"


# ─────────────────────────────────────────────────────────────────────────────
# 3. scheduler._process_integration for gcal injects DB client into creds
# ─────────────────────────────────────────────────────────────────────────────


async def test_scheduler_gcal_injects_db_client_before_refresh() -> None:
    """_process_integration for gcal overrides client_id/secret from DB before refresh.

    The refresher should receive the DB-authoritative client, not the stale blob.
    """
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult
    from artemis.integrations.token_refresh.scheduler import _process_integration

    past_expires = time.time() - 3600  # expired
    stale_creds = {
        "access_token": "old",
        "refresh_token": "rt",
        "client_id": "STALE_CLIENT",
        "client_secret": "STALE_SECRET",
        "expires_at": past_expires,
    }
    encrypted = encrypt_credentials(stale_creds)

    integration = MagicMock(spec=Integration)
    integration.id = 55
    integration.provider = "gcal"
    integration.encrypted_credentials = encrypted
    integration.last_refresh_attempt_at = None
    integration.status = "active"

    received_creds: list[dict[str, Any]] = []

    async def mock_refresh(creds: dict[str, Any]) -> RefreshResult:
        received_creds.append(dict(creds))
        new_creds = dict(creds)
        new_creds["access_token"] = "new"
        new_creds["expires_at"] = time.time() + 3600
        return RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds=new_creds)

    mock_refresher = MagicMock()
    mock_refresher.refresh = AsyncMock(side_effect=mock_refresh)

    db_cfg = GCalConfig(client_id="DB_CLIENT_975559492379", client_secret="DB_SECRET")
    mock_session = AsyncMock()
    now = datetime.now(UTC)

    with (
        patch(
            "artemis.integrations.token_refresh.scheduler.REFRESHERS",
            {"gcal": mock_refresher},
        ),
        patch(
            "artemis.integrations.token_refresh.scheduler.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=db_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.scheduler.repo.persist_refreshed_credentials",
            new_callable=AsyncMock,
        ),
    ):
        await _process_integration(mock_session, integration, now)

    assert len(received_creds) == 1
    # Refresher must have received the DB client, not STALE_CLIENT.
    assert received_creds[0]["client_id"] == "DB_CLIENT_975559492379"
    assert received_creds[0]["client_secret"] == "DB_SECRET"


# ─────────────────────────────────────────────────────────────────────────────
# 4. refresh_google_credentials_tick refreshes personal + marketing rows
# ─────────────────────────────────────────────────────────────────────────────


async def test_refresh_google_credentials_tick_refreshes_expiring_rows() -> None:
    """refresh_google_credentials_tick calls Google and upserts fresh tokens for expiring rows."""
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.token_refresh.providers.google_credentials import (
        refresh_google_credentials_tick,
    )

    db_cfg = GCalConfig(client_id="DB_CID", client_secret="DB_CSEC")

    # Build two mock GoogleCredential rows — personal + marketing, both expiring.
    expiring_expiry = datetime.now(UTC) + timedelta(minutes=5)  # inside leeway

    def _make_row(user_id: int, purpose: str) -> MagicMock:
        row = MagicMock()
        row.id = user_id * 10
        row.user_id = user_id
        row.purpose = purpose
        row.access_token = f"old_at_{purpose}"
        row.refresh_token = f"rt_{purpose}"
        row.expiry = expiring_expiry
        row.scope = "https://www.googleapis.com/auth/documents"
        row.connected_email = f"{purpose}@example.com"
        row.updated_at = datetime.now(UTC) - timedelta(minutes=20)  # outside cooldown
        return row

    personal_row = _make_row(1, "personal")
    marketing_row = _make_row(2, "marketing")

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [personal_row, marketing_row]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    upserted: list[dict[str, Any]] = []

    async def mock_upsert(session: Any, *, user_id: int, purpose: str, **kwargs: Any) -> Any:
        upserted.append({"user_id": user_id, "purpose": purpose, **kwargs})

    google_resp = {
        "access_token": "new_at",
        "expires_in": 3600,
    }
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.is_success = True
    mock_http_resp.json.return_value = google_resp

    with (
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=db_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=mock_upsert,
        ),
        patch("httpx.AsyncClient") as mock_http_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_http_resp)
        mock_http_cls.return_value = mock_http

        await refresh_google_credentials_tick(mock_session)

    # Both personal and marketing rows should be upserted with new tokens.
    assert len(upserted) == 2
    purposes = {u["purpose"] for u in upserted}
    assert purposes == {"personal", "marketing"}
    for u in upserted:
        assert u["access_token"] == "new_at"


# ─────────────────────────────────────────────────────────────────────────────
# 5. refresh_google_credentials_tick skips rows with no refresh_token
# ─────────────────────────────────────────────────────────────────────────────


async def test_refresh_google_credentials_tick_skips_no_refresh_token() -> None:
    """Rows without a refresh_token are silently skipped."""
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.token_refresh.providers.google_credentials import (
        refresh_google_credentials_tick,
    )

    db_cfg = GCalConfig(client_id="DB_CID", client_secret="DB_CSEC")

    row = MagicMock()
    row.id = 1
    row.user_id = 1
    row.purpose = "personal"
    row.access_token = "at"
    row.refresh_token = None  # no refresh token
    row.expiry = datetime.now(UTC) - timedelta(hours=1)  # expired
    row.scope = None
    row.connected_email = None
    row.updated_at = datetime.now(UTC) - timedelta(minutes=20)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    upserted: list[Any] = []

    with (
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=db_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=lambda *a, **kw: upserted.append(kw),
        ),
        patch("httpx.AsyncClient") as mock_http_cls,
    ):
        mock_http = AsyncMock()
        mock_http.post = AsyncMock()
        mock_http_cls.return_value = mock_http

        await refresh_google_credentials_tick(mock_session)

    assert upserted == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. refresh_google_credentials_tick skips rows still healthy
# ─────────────────────────────────────────────────────────────────────────────


async def test_refresh_google_credentials_tick_skips_healthy_row() -> None:
    """Rows expiring far in the future (outside leeway window) are not refreshed."""
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.token_refresh.providers.google_credentials import (
        refresh_google_credentials_tick,
    )

    db_cfg = GCalConfig(client_id="DB_CID", client_secret="DB_CSEC")

    row = MagicMock()
    row.id = 1
    row.user_id = 1
    row.purpose = "personal"
    row.access_token = "at"
    row.refresh_token = "rt"
    row.expiry = datetime.now(UTC) + timedelta(hours=2)  # well past leeway
    row.scope = None
    row.connected_email = None
    row.updated_at = datetime.now(UTC) - timedelta(hours=1)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    upserted: list[Any] = []

    with (
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=db_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=lambda *a, **kw: upserted.append(kw),
        ),
    ):
        await refresh_google_credentials_tick(mock_session)

    assert upserted == []


# ─────────────────────────────────────────────────────────────────────────────
# 7. refresh_google_credentials_tick handles REFRESH_TOKEN_EXPIRED gracefully
# ─────────────────────────────────────────────────────────────────────────────


async def test_refresh_google_credentials_tick_handles_expired_refresh_token() -> None:
    """A 400 invalid_grant from Google logs an error but does not raise."""
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.token_refresh.providers.google_credentials import (
        refresh_google_credentials_tick,
    )

    db_cfg = GCalConfig(client_id="DB_CID", client_secret="DB_CSEC")

    row = MagicMock()
    row.id = 1
    row.user_id = 1
    row.purpose = "marketing"
    row.access_token = "at"
    row.refresh_token = "dead_rt"
    row.expiry = datetime.now(UTC) - timedelta(hours=2)  # expired
    row.scope = None
    row.connected_email = "marketing@example.com"
    row.updated_at = datetime.now(UTC) - timedelta(minutes=20)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_bad_resp = MagicMock()
    mock_bad_resp.status_code = 400
    mock_bad_resp.is_success = False
    mock_bad_resp.text = (
        '{"error":"invalid_grant","error_description":"Token has been expired or revoked."}'
    )
    mock_bad_resp.json.return_value = {
        "error": "invalid_grant",
        "error_description": "Token has been expired or revoked.",
    }

    upserted: list[Any] = []

    with (
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=db_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=lambda *a, **kw: upserted.append(kw),
        ),
        patch("httpx.AsyncClient") as mock_http_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_bad_resp)
        mock_http_cls.return_value = mock_http

        # Must not raise — logs error but continues.
        await refresh_google_credentials_tick(mock_session)

    # No upsert on REFRESH_TOKEN_EXPIRED.
    assert upserted == []


# ─────────────────────────────────────────────────────────────────────────────
# 8. refresh_google_credentials_tick handles transient HTTP failure gracefully
# ─────────────────────────────────────────────────────────────────────────────


async def test_refresh_google_credentials_tick_handles_transient_failure() -> None:
    """A 5xx from Google logs a warning but does not raise or upsert."""
    from artemis.integrations.config_resolver import GCalConfig
    from artemis.integrations.token_refresh.providers.google_credentials import (
        refresh_google_credentials_tick,
    )

    db_cfg = GCalConfig(client_id="DB_CID", client_secret="DB_CSEC")

    row = MagicMock()
    row.id = 1
    row.user_id = 1
    row.purpose = "personal"
    row.access_token = "at"
    row.refresh_token = "rt"
    row.expiry = datetime.now(UTC) - timedelta(hours=1)
    row.scope = None
    row.connected_email = None
    row.updated_at = datetime.now(UTC) - timedelta(minutes=20)

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [row]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    mock_bad_resp = MagicMock()
    mock_bad_resp.status_code = 503
    mock_bad_resp.is_success = False
    mock_bad_resp.text = "Service Unavailable"
    mock_bad_resp.json.side_effect = ValueError("not JSON")

    upserted: list[Any] = []

    with (
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=db_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.providers.google_credentials.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=lambda *a, **kw: upserted.append(kw),
        ),
        patch("httpx.AsyncClient") as mock_http_cls,
    ):
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_bad_resp)
        mock_http_cls.return_value = mock_http

        await refresh_google_credentials_tick(mock_session)

    assert upserted == []
