"""Unit tests for GCal token auto-refresh + auth-dead visibility fix.

Covers:
  1. GCalClient._refresh() success → calls on_tokens_refreshed with correct args
  2. GCalClient._refresh() 400 invalid_grant → raises GCalAuthDeadError
  3. find_recently_ended_meetings when list_events raises GCalAuthDeadError
     → mark_needs_reauth called + DM attempted + returns []
  4. scheduler._process_integration with expires_at=0 (expired) → calls refresher
     + persist_refreshed_credentials with a future expires_at
  5. scheduler._process_integration with NO expires_at (legacy row) → SKIPS (no regression)
  6. sync_personal_google_integrations writes expires_at into the encrypted creds

No real network calls — all external HTTP is mocked.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# 1. GCalClient._refresh() success → on_tokens_refreshed called
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_client_refresh_success_calls_callback() -> None:
    """Successful token refresh calls on_tokens_refreshed with access_token and expires_at."""
    from artemis.integrations.gcal.client import GCalClient

    callback_args: list[dict[str, Any]] = []

    async def on_refreshed(access_token: str, refresh_token: str, expires_at: float) -> None:
        callback_args.append(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
            }
        )

    client = GCalClient(
        access_token="old_tok",
        refresh_token="refresh_tok",
        client_id="cid",
        client_secret="csec",
        expires_at=0.0,
        on_tokens_refreshed=on_refreshed,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new_tok",
        "expires_in": 3600,
    }

    before = time.time()

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http

        await client._refresh()

    # In-memory token updated
    assert client.access_token == "new_tok"

    # Callback fired once with correct args
    assert len(callback_args) == 1
    assert callback_args[0]["access_token"] == "new_tok"
    assert callback_args[0]["refresh_token"] == "refresh_tok"  # Google didn't rotate
    after = time.time()
    # expires_at should be roughly now + 3600
    assert before + 3590 <= callback_args[0]["expires_at"] <= after + 3610


# ─────────────────────────────────────────────────────────────────────────────
# 2. GCalClient._refresh() 400 invalid_grant → GCalAuthDeadError
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_client_refresh_400_raises_auth_dead() -> None:
    """A 400 response from the token endpoint raises GCalAuthDeadError."""
    from artemis.integrations.gcal.client import GCalAuthDeadError, GCalClient

    client = GCalClient(
        access_token="tok",
        refresh_token="bad_rt",
        client_id="cid",
        client_secret="csec",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.is_success = False
    mock_resp.text = '{"error":"invalid_grant"}'
    mock_resp.raise_for_status = MagicMock(side_effect=Exception("should not be called"))

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http

        with pytest.raises(GCalAuthDeadError):
            await client._refresh()


# ─────────────────────────────────────────────────────────────────────────────
# 3. find_recently_ended_meetings: GCalAuthDeadError → mark_needs_reauth + DM
# ─────────────────────────────────────────────────────────────────────────────


async def test_find_recently_ended_gcal_auth_dead() -> None:
    """When list_events raises GCalAuthDeadError, mark_needs_reauth is called
    and a Slack DM is attempted; the function returns []."""
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.gcal.client import GCalAuthDeadError
    from artemis.meetings.summarizer import find_recently_ended_meetings

    creds = {
        "access_token": "old",
        "refresh_token": "bad_rt",
        "client_id": "cid",
        "client_secret": "csec",
        "expires_at": 0.0,
    }
    encrypted = encrypt_credentials(creds)
    mock_row = MagicMock()
    mock_row.encrypted_credentials = encrypted
    mock_row.id = 42

    mock_db_result = MagicMock()
    mock_db_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    mark_needs_reauth_called: list[int] = []
    dm_sent: list[bool] = []

    async def mock_mark_needs_reauth(session: Any, integration_id: int) -> None:
        mark_needs_reauth_called.append(integration_id)

    async def mock_send_owner_dm(session: Any) -> None:
        dm_sent.append(True)

    from artemis.integrations.config_resolver import GCalConfig

    mock_gcal_cfg = GCalConfig(client_id="cid", client_secret="csec")

    # list_active returns our mock row, then returns it again on the second call
    # (the one inside find_recently_ended_meetings for gcal_integration_id).
    with (
        patch(
            "artemis.integrations.repository.list_active",
            new_callable=AsyncMock,
            return_value=[mock_row],
        ),
        patch(
            "artemis.meetings.summarizer.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=mock_gcal_cfg,
        ),
        patch(
            "artemis.integrations.gcal.client.GCalClient.list_events",
            new_callable=AsyncMock,
            side_effect=GCalAuthDeadError("revoked"),
        ),
        patch(
            "artemis.integrations.gcal.auth_dead.repo.mark_needs_reauth",
            new_callable=AsyncMock,
            side_effect=mock_mark_needs_reauth,
        ),
        patch(
            "artemis.integrations.gcal.auth_dead.repo.get_by_id",
            new_callable=AsyncMock,
            return_value=MagicMock(status="active"),
        ),
        patch(
            "artemis.integrations.gcal.auth_dead._send_owner_dm",
            new_callable=AsyncMock,
            side_effect=mock_send_owner_dm,
        ),
    ):
        result = await find_recently_ended_meetings(mock_session)

    assert result == []
    assert 42 in mark_needs_reauth_called
    assert dm_sent == [True]


# ─────────────────────────────────────────────────────────────────────────────
# 4. Scheduler: expired expires_at → calls refresher + persist_refreshed_credentials
# ─────────────────────────────────────────────────────────────────────────────


async def test_scheduler_process_integration_expired_refreshes() -> None:
    """_process_integration with expires_at=0 (in the past) calls the refresher
    and persists credentials with a future expires_at."""
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult
    from artemis.integrations.token_refresh.scheduler import _process_integration

    past_expires = time.time() - 7200  # expired 2 hours ago
    creds = {
        "access_token": "old_tok",
        "refresh_token": "rt",
        "client_id": "cid",
        "client_secret": "csec",
        "expires_at": past_expires,
    }
    encrypted = encrypt_credentials(creds)

    integration = MagicMock(spec=Integration)
    integration.id = 99
    integration.provider = "gcal"
    integration.encrypted_credentials = encrypted
    integration.last_refresh_attempt_at = None
    integration.status = "active"

    future_expires = time.time() + 3600
    new_creds = dict(creds)
    new_creds["access_token"] = "new_tok"
    new_creds["expires_at"] = future_expires

    persisted: list[dict[str, Any]] = []

    async def mock_persist(session: Any, *, integration_id: int, new_creds: dict[str, Any]) -> None:
        persisted.append(new_creds)

    mock_refresher = AsyncMock()
    mock_refresher.refresh = AsyncMock(
        return_value=RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds=new_creds)
    )

    mock_session = AsyncMock()
    now = datetime.now(UTC)

    from artemis.integrations.config_resolver import GCalConfig

    mock_gcal_cfg = GCalConfig(client_id="cid", client_secret="csec")

    with (
        patch(
            "artemis.integrations.token_refresh.scheduler.REFRESHERS",
            {"gcal": mock_refresher},
        ),
        patch(
            "artemis.integrations.token_refresh.scheduler.resolve_gcal_config",
            new_callable=AsyncMock,
            return_value=mock_gcal_cfg,
        ),
        patch(
            "artemis.integrations.token_refresh.scheduler.repo.persist_refreshed_credentials",
            new_callable=AsyncMock,
            side_effect=mock_persist,
        ),
    ):
        await _process_integration(mock_session, integration, now)

    assert len(persisted) == 1
    assert persisted[0]["access_token"] == "new_tok"
    assert float(str(persisted[0]["expires_at"])) > time.time()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Scheduler: NO expires_at (legacy row) → skip (regression guard)
# ─────────────────────────────────────────────────────────────────────────────


async def test_scheduler_process_integration_no_expires_at_skips() -> None:
    """Legacy row with no expires_at still skips refresh (regression guard).

    New rows will always have expires_at after the fix, but existing legacy rows
    (like the live gcal row id=2) must not cause crashes — the scheduler should
    skip them silently until they are updated at next connect or live bootstrap.
    """
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.models import Integration
    from artemis.integrations.token_refresh.scheduler import _process_integration

    creds_no_expiry = {
        "access_token": "old_tok",
        "refresh_token": "rt",
        "client_id": "cid",
        "client_secret": "csec",
        # Intentionally no expires_at
    }
    encrypted = encrypt_credentials(creds_no_expiry)

    integration = MagicMock(spec=Integration)
    integration.id = 2
    integration.provider = "gcal"
    integration.encrypted_credentials = encrypted
    integration.last_refresh_attempt_at = None
    integration.status = "active"

    refresher_called: list[bool] = []
    mock_refresher = AsyncMock()

    async def mock_refresh(creds: Any) -> Any:
        refresher_called.append(True)
        from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult

        return RefreshResult(outcome=RefreshOutcome.STILL_VALID)

    mock_refresher.refresh = AsyncMock(side_effect=mock_refresh)

    mock_session = AsyncMock()
    now = datetime.now(UTC)

    with patch(
        "artemis.integrations.token_refresh.scheduler.REFRESHERS",
        {"gcal": mock_refresher},
    ):
        await _process_integration(mock_session, integration, now)

    # Refresher should NOT be called — no expires_at means skip
    assert refresher_called == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. sync_personal_google_integrations writes expires_at into encrypted creds
# ─────────────────────────────────────────────────────────────────────────────


async def test_sync_personal_google_integrations_writes_expires_at() -> None:
    """sync_personal_google_integrations includes expires_at in the credentials blob."""
    from artemis.google_integration import sync_personal_google_integrations
    from artemis.integrations.crypto import decrypt_credentials

    # Build a minimal GoogleCredential-like mock.
    mock_credential = MagicMock()
    mock_credential.connected_email = "jon@amiralearning.com"
    mock_credential.access_token = "at123"
    mock_credential.refresh_token = "rt456"
    mock_credential.scope = (
        "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events"
    )

    captured_creds: list[dict[str, Any]] = []

    async def mock_upsert_integration(
        session: Any,
        *,
        provider: str,
        workspace_id: str,
        encrypted_credentials: bytes,
        **kwargs: Any,
    ) -> Any:
        captured_creds.append(decrypt_credentials(encrypted_credentials))
        return MagicMock()

    mock_session = AsyncMock()
    future_expires = time.time() + 3600

    with patch(
        "artemis.google_integration.integrations_repo.upsert_integration",
        new_callable=AsyncMock,
        side_effect=mock_upsert_integration,
    ):
        await sync_personal_google_integrations(
            mock_session,
            credential=mock_credential,
            client_id="cid",
            client_secret="csec",
            expires_at=future_expires,
        )

    assert len(captured_creds) == 1
    stored = captured_creds[0]
    assert "expires_at" in stored
    stored_expires = float(str(stored["expires_at"]))
    # Should be within a few seconds of what we passed
    assert abs(stored_expires - future_expires) < 5


async def test_sync_personal_google_integrations_fallback_expires_at() -> None:
    """When expires_at=None, a ~1-hour fallback is stored."""
    from artemis.google_integration import sync_personal_google_integrations
    from artemis.integrations.crypto import decrypt_credentials

    mock_credential = MagicMock()
    mock_credential.connected_email = "jon@amiralearning.com"
    mock_credential.access_token = "at123"
    mock_credential.refresh_token = "rt456"
    mock_credential.scope = (
        "https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/calendar.events"
    )

    captured_creds: list[dict[str, Any]] = []

    async def mock_upsert_integration(
        session: Any,
        *,
        provider: str,
        workspace_id: str,
        encrypted_credentials: bytes,
        **kwargs: Any,
    ) -> Any:
        captured_creds.append(decrypt_credentials(encrypted_credentials))
        return MagicMock()

    mock_session = AsyncMock()
    before = time.time()

    with patch(
        "artemis.google_integration.integrations_repo.upsert_integration",
        new_callable=AsyncMock,
        side_effect=mock_upsert_integration,
    ):
        await sync_personal_google_integrations(
            mock_session,
            credential=mock_credential,
            client_id="cid",
            client_secret="csec",
            expires_at=None,  # No expiry provided
        )

    after = time.time()
    assert len(captured_creds) == 1
    stored = captured_creds[0]
    assert "expires_at" in stored
    stored_expires = float(str(stored["expires_at"]))
    # Fallback: ~1 hour from now
    assert before + 3580 <= stored_expires <= after + 3620


# ─────────────────────────────────────────────────────────────────────────────
# 7. GCalClient._refresh() success → rotated refresh_token picked up
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_client_refresh_rotates_refresh_token() -> None:
    """When Google returns a new refresh_token, it is stored and forwarded to callback."""
    from artemis.integrations.gcal.client import GCalClient

    callback_args: list[dict[str, Any]] = []

    async def on_refreshed(access_token: str, refresh_token: str, expires_at: float) -> None:
        callback_args.append({"access_token": access_token, "refresh_token": refresh_token})

    client = GCalClient(
        access_token="old_tok",
        refresh_token="old_rt",
        client_id="cid",
        client_secret="csec",
        on_tokens_refreshed=on_refreshed,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new_tok",
        "refresh_token": "new_rt",  # Google rotated it
        "expires_in": 3600,
    }

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http

        await client._refresh()

    assert client.access_token == "new_tok"
    assert len(callback_args) == 1
    assert callback_args[0]["refresh_token"] == "new_rt"


# ─────────────────────────────────────────────────────────────────────────────
# 8. GCal provider.connect() writes expires_at
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_provider_connect_writes_expires_at() -> None:
    """GCalProvider.connect() stores expires_at in the encrypted credentials."""
    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.gcal.provider import GCalProvider

    token_resp_data = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 3600,
        "token_type": "Bearer",
    }
    info_resp_data = {"email": "jon@test.com"}

    mock_token_resp = MagicMock()
    mock_token_resp.status_code = 200
    mock_token_resp.is_success = True
    mock_token_resp.raise_for_status = MagicMock()
    mock_token_resp.json.return_value = token_resp_data

    mock_info_resp = MagicMock()
    mock_info_resp.status_code = 200
    mock_info_resp.is_success = True
    mock_info_resp.raise_for_status = MagicMock()
    mock_info_resp.json.return_value = info_resp_data

    before = time.time()

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_token_resp)
        mock_http.get = AsyncMock(return_value=mock_info_resp)
        mock_http_cls.return_value = mock_http

        provider = GCalProvider(
            client_id="cid", client_secret="csec", redirect_uri="https://example.com/cb"
        )
        integration = await provider.connect("code123")

    after = time.time()

    creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    assert "expires_at" in creds
    stored_expires = float(str(creds["expires_at"]))
    assert before + 3590 <= stored_expires <= after + 3610
