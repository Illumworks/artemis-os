"""Unit tests for Gmail token auto-refresh + auth-dead visibility fix.

Covers:
  1. GmailClient._refresh() success → calls on_tokens_refreshed with correct args
  2. GmailClient._refresh() success → rotated refresh_token picked up
  3. GmailClient._refresh() 400 invalid_grant → raises GmailAuthDeadError
  4. handle_gmail_auth_dead: marks the gcal integration needs_reauth + sends DM
  5. handle_gmail_auth_dead: suppresses DM when already needs_reauth (rate-limit)
  6. fetch_gmail_awaiting_reply: GmailAuthDeadError → handle_gmail_auth_dead called, returns []
  7. fetch_gmail_awaiting_reply: _resolve_gmail_creds uses google_credentials (personal),
     not the missing gmail integrations row

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
# 1. GmailClient._refresh() success → on_tokens_refreshed called
# ─────────────────────────────────────────────────────────────────────────────


async def test_gmail_client_refresh_success_calls_callback() -> None:
    """Successful token refresh calls on_tokens_refreshed with access_token and expires_at."""
    from artemis.integrations.gmail.client import GmailClient

    callback_args: list[dict[str, Any]] = []

    async def on_refreshed(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        callback_args.append(
            {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expires_at": expires_at,
            }
        )

    client = GmailClient(
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

    after = time.time()

    # In-memory token updated
    assert client.access_token == "new_tok"

    # Callback fired once with correct args
    assert len(callback_args) == 1
    assert callback_args[0]["access_token"] == "new_tok"
    assert callback_args[0]["refresh_token"] == "refresh_tok"  # Google didn't rotate
    # expires_at should be roughly now + 3600
    assert before + 3590 <= callback_args[0]["expires_at"] <= after + 3610


# ─────────────────────────────────────────────────────────────────────────────
# 2. GmailClient._refresh() success → rotated refresh_token picked up
# ─────────────────────────────────────────────────────────────────────────────


async def test_gmail_client_refresh_rotates_refresh_token() -> None:
    """When Google returns a new refresh_token, it is stored and forwarded to callback."""
    from artemis.integrations.gmail.client import GmailClient

    callback_args: list[dict[str, Any]] = []

    async def on_refreshed(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        callback_args.append({"access_token": access_token, "refresh_token": refresh_token})

    client = GmailClient(
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
# 3. GmailClient._refresh() 400 invalid_grant → GmailAuthDeadError
# ─────────────────────────────────────────────────────────────────────────────


async def test_gmail_client_refresh_400_raises_auth_dead() -> None:
    """A 400 response from the token endpoint raises GmailAuthDeadError."""
    from artemis.integrations.gmail.client import GmailAuthDeadError, GmailClient

    client = GmailClient(
        access_token="tok",
        refresh_token="bad_rt",
        client_id="cid",
        client_secret="csec",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.is_success = False
    mock_resp.text = '{"error":"invalid_grant"}'
    mock_resp.raise_for_status = MagicMock(
        side_effect=Exception("should not be called")
    )

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http

        with pytest.raises(GmailAuthDeadError):
            await client._refresh()


# ─────────────────────────────────────────────────────────────────────────────
# 4. handle_gmail_auth_dead: marks gcal integration + sends DM on first detection
# ─────────────────────────────────────────────────────────────────────────────


async def test_handle_gmail_auth_dead_marks_and_dms() -> None:
    """handle_gmail_auth_dead marks needs_reauth on the gcal row and sends a DM."""
    from artemis.integrations.gmail.auth_dead import handle_gmail_auth_dead

    mock_integration = MagicMock()
    mock_integration.id = 2
    mock_integration.status = "active"  # First detection — not yet needs_reauth

    mark_called: list[int] = []
    dm_sent: list[bool] = []

    async def mock_mark_needs_reauth(session: Any, integration_id: int) -> None:
        mark_called.append(integration_id)

    async def mock_send_owner_dm(session: Any) -> None:
        dm_sent.append(True)

    mock_session = AsyncMock()

    with (
        patch(
            "artemis.integrations.gmail.auth_dead.repo.list_active",
            new_callable=AsyncMock,
            return_value=[mock_integration],
        ),
        patch(
            "artemis.integrations.gmail.auth_dead.repo.mark_needs_reauth",
            new_callable=AsyncMock,
            side_effect=mock_mark_needs_reauth,
        ),
        patch(
            "artemis.integrations.gmail.auth_dead._send_owner_dm",
            new_callable=AsyncMock,
            side_effect=mock_send_owner_dm,
        ),
    ):
        await handle_gmail_auth_dead(mock_session)

    assert 2 in mark_called
    assert dm_sent == [True]


# ─────────────────────────────────────────────────────────────────────────────
# 5. handle_gmail_auth_dead: DM suppressed when already needs_reauth
# ─────────────────────────────────────────────────────────────────────────────


async def test_handle_gmail_auth_dead_suppresses_dm_when_already_alerted() -> None:
    """handle_gmail_auth_dead skips the DM when the row is already needs_reauth."""
    from artemis.integrations.gmail.auth_dead import handle_gmail_auth_dead

    mock_integration = MagicMock()
    mock_integration.id = 2
    mock_integration.status = "needs_reauth"  # Already alerted

    mark_called: list[int] = []
    dm_sent: list[bool] = []

    async def mock_mark_needs_reauth(session: Any, integration_id: int) -> None:
        mark_called.append(integration_id)

    async def mock_send_owner_dm(session: Any) -> None:
        dm_sent.append(True)

    mock_session = AsyncMock()

    with (
        patch(
            "artemis.integrations.gmail.auth_dead.repo.list_active",
            new_callable=AsyncMock,
            return_value=[mock_integration],
        ),
        patch(
            "artemis.integrations.gmail.auth_dead.repo.mark_needs_reauth",
            new_callable=AsyncMock,
            side_effect=mock_mark_needs_reauth,
        ),
        patch(
            "artemis.integrations.gmail.auth_dead._send_owner_dm",
            new_callable=AsyncMock,
            side_effect=mock_send_owner_dm,
        ),
    ):
        await handle_gmail_auth_dead(mock_session)

    # mark_needs_reauth still called (idempotent)
    assert 2 in mark_called
    # But DM suppressed — already alerted
    assert dm_sent == []


# ─────────────────────────────────────────────────────────────────────────────
# 6. fetch_gmail_awaiting_reply: GmailAuthDeadError → handle_gmail_auth_dead + []
# ─────────────────────────────────────────────────────────────────────────────


async def test_fetch_gmail_awaiting_reply_auth_dead_triggers_handler() -> None:
    """When list_recent_messages raises GmailAuthDeadError, handle_gmail_auth_dead
    is called and the function returns []."""
    from artemis.integrations.gmail.client import GmailAuthDeadError
    from artemis.proactivity.radar import fetch_gmail_awaiting_reply

    future_expiry = datetime.fromtimestamp(time.time() + 3600, tz=UTC)

    mock_credential = MagicMock()
    mock_credential.purpose = "personal"
    mock_credential.access_token = "tok"
    mock_credential.refresh_token = "rt"
    mock_credential.expiry = future_expiry
    mock_credential.scope = (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/gmail.send"
    )

    mock_db_result = MagicMock()
    mock_db_result.scalar_one_or_none.return_value = mock_credential

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    auth_dead_called: list[bool] = []

    async def mock_handle_gmail_auth_dead(session: Any) -> None:
        auth_dead_called.append(True)

    mock_config = MagicMock()
    mock_config.client_id = "cid"
    mock_config.client_secret = "csec"

    with (
        patch(
            "artemis.google_integration.resolve_google_oauth_client_config",
            new_callable=AsyncMock,
            return_value=mock_config,
        ),
        patch(
            "artemis.integrations.gmail.client.GmailClient.list_recent_messages",
            new_callable=AsyncMock,
            side_effect=GmailAuthDeadError("revoked"),
        ),
        patch(
            "artemis.integrations.gmail.auth_dead.handle_gmail_auth_dead",
            new_callable=AsyncMock,
            side_effect=mock_handle_gmail_auth_dead,
        ),
    ):
        result = await fetch_gmail_awaiting_reply(mock_session)

    assert result == []
    assert auth_dead_called == [True]


# ─────────────────────────────────────────────────────────────────────────────
# 7. _resolve_gmail_creds uses google_credentials (personal), not integrations
# ─────────────────────────────────────────────────────────────────────────────


async def test_resolve_gmail_creds_uses_google_credentials_table() -> None:
    """_resolve_gmail_creds returns credentials from the personal google_credentials
    row — NOT from a (non-existent) gmail integrations row."""
    from artemis.proactivity.radar import _resolve_gmail_creds

    future_expiry = datetime.fromtimestamp(time.time() + 3600, tz=UTC)

    mock_credential = MagicMock()
    mock_credential.purpose = "personal"
    mock_credential.access_token = "access_tok_xyz"
    mock_credential.refresh_token = "refresh_tok_xyz"
    mock_credential.expiry = future_expiry
    mock_credential.scope = (
        "https://www.googleapis.com/auth/gmail.readonly "
        "https://www.googleapis.com/auth/calendar"
    )

    mock_db_result = MagicMock()
    mock_db_result.scalar_one_or_none.return_value = mock_credential

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    mock_config = MagicMock()
    mock_config.client_id = "cid"
    mock_config.client_secret = "csec"

    with patch(
        "artemis.google_integration.resolve_google_oauth_client_config",
        new_callable=AsyncMock,
        return_value=mock_config,
    ):
        result = await _resolve_gmail_creds(mock_session)

    assert result is not None
    creds, expires_at = result
    assert creds["access_token"] == "access_tok_xyz"
    assert creds["refresh_token"] == "refresh_tok_xyz"
    assert creds["client_id"] == "cid"
    assert creds["client_secret"] == "csec"
    assert abs(expires_at - future_expiry.timestamp()) < 2


async def test_resolve_gmail_creds_returns_none_when_no_credential() -> None:
    """_resolve_gmail_creds returns None when no personal Google credential exists."""
    from artemis.proactivity.radar import _resolve_gmail_creds

    mock_db_result = MagicMock()
    mock_db_result.scalar_one_or_none.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    result = await _resolve_gmail_creds(mock_session)
    assert result is None


async def test_resolve_gmail_creds_returns_none_without_gmail_scope() -> None:
    """_resolve_gmail_creds returns None when the credential lacks gmail.readonly scope."""
    from artemis.proactivity.radar import _resolve_gmail_creds

    future_expiry = datetime.fromtimestamp(time.time() + 3600, tz=UTC)

    mock_credential = MagicMock()
    mock_credential.purpose = "personal"
    mock_credential.access_token = "tok"
    mock_credential.refresh_token = "rt"
    mock_credential.expiry = future_expiry
    # Only calendar scope — no gmail.readonly
    mock_credential.scope = "https://www.googleapis.com/auth/calendar"

    mock_db_result = MagicMock()
    mock_db_result.scalar_one_or_none.return_value = mock_credential

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    result = await _resolve_gmail_creds(mock_session)
    assert result is None
