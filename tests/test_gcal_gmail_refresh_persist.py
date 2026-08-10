"""Unit tests for the GCal + Gmail refresh-and-persist path.

Verifies the specific bugs fixed in this PR:

GCal:
  1. routes/calendar.py _get_gcal_client now uses _client_from_row (has persist callback)
  2. GCalClient on-the-fly 401-refresh persists the new token via the callback
  3. GCalProvider.verify() persists refreshed token via _persist_refreshed_tokens
  4. _refresh_tokens() captures rotated refresh_token
  5. PeopleClient._refresh() fires on_tokens_refreshed callback + captures rotated token

Gmail:
  6. _resolve_gmail_client callback opens a FRESH session (not the expired one)
  7. _resolve_gmail_client callback uses upsert_google_credential (not session.commit)
  8. Gmail _on_tokens_refreshed: on failure, error is logged but not re-raised

All tests are offline — no real network or DB access.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────────────────────
# 1. routes/calendar._get_gcal_client uses _client_from_row (has persist callback)
# ─────────────────────────────────────────────────────────────────────────────


async def test_calendar_get_gcal_client_uses_client_from_row() -> None:
    """_get_gcal_client must delegate to _client_from_row, which wires the persist callback.

    Previously it constructed a bare GCalClient() with no on_tokens_refreshed —
    the refreshed token was discarded at end-of-request.
    """
    from artemis.routes.calendar import _get_gcal_client

    mock_row = MagicMock()
    mock_row.id = 7
    mock_row.encrypted_credentials = b"enc"

    mock_client = MagicMock()
    mock_session = AsyncMock()

    with (
        patch(
            "artemis.routes.calendar.repo.list_active",
            new_callable=AsyncMock,
            return_value=[mock_row],
        ),
        patch(
            "artemis.routes.calendar._client_from_row",
            new_callable=AsyncMock,
            return_value=mock_client,
        ) as mock_from_row,
    ):
        result = await _get_gcal_client(mock_session)

    assert result is mock_client
    mock_from_row.assert_called_once_with(mock_row, mock_session)


# ─────────────────────────────────────────────────────────────────────────────
# 2. GCalClient on-the-fly 401 triggers refresh + callback is called
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_client_401_triggers_refresh_and_callback() -> None:
    """When GCalClient._get() hits a 401, it refreshes and the callback fires."""
    from artemis.integrations.gcal.client import GCalClient

    callback_called: list[dict[str, Any]] = []

    async def on_refreshed(access_token: str, refresh_token: str, expires_at: float) -> None:
        callback_called.append({"access_token": access_token, "refresh_token": refresh_token})

    client = GCalClient(
        access_token="expired_tok",
        refresh_token="rt",
        client_id="cid",
        client_secret="csec",
        expires_at=0.0,
        on_tokens_refreshed=on_refreshed,
    )

    # First GET: 401.  After refresh: 200.
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.is_success = False

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.is_success = True
    resp_200.json.return_value = {"items": []}

    # Token refresh response
    refresh_resp = MagicMock()
    refresh_resp.status_code = 200
    refresh_resp.is_success = True
    refresh_resp.json.return_value = {"access_token": "new_tok", "expires_in": 3600}

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=[resp_401, resp_200])
    mock_http.post = AsyncMock(return_value=refresh_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        await client._get("/users/me/calendarList")

    assert client.access_token == "new_tok"
    assert len(callback_called) == 1
    assert callback_called[0]["access_token"] == "new_tok"
    assert callback_called[0]["refresh_token"] == "rt"  # not rotated


# ─────────────────────────────────────────────────────────────────────────────
# 3. GCalProvider.verify() persists refreshed token via _persist_refreshed_tokens
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_provider_verify_persists_on_401_then_success() -> None:
    """verify() should call _persist_refreshed_tokens when the 401-refresh succeeds."""
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.gcal.provider import GCalProvider

    creds = {
        "access_token": "expired",
        "refresh_token": "rt",
        "client_id": "cid",
        "client_secret": "csec",
        "expires_at": 0.0,
    }
    encrypted = encrypt_credentials(creds)

    mock_integration = MagicMock()
    mock_integration.id = 5
    mock_integration.encrypted_credentials = encrypted

    # List-endpoint: 401, then 200 after refresh
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.is_success = False

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.is_success = True
    resp_200.json.return_value = {}

    persist_called: list[dict[str, Any]] = []

    async def mock_persist(
        *,
        integration_id: int,
        existing_creds: dict[str, object],
        new_access: str,
        new_refresh: str,
        new_expires_at: float,
    ) -> None:
        persist_called.append(
            {
                "integration_id": integration_id,
                "new_access": new_access,
                "new_refresh": new_refresh,
            }
        )

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.get = AsyncMock(side_effect=[resp_401, resp_200])
    mock_http.post = AsyncMock(
        return_value=MagicMock(
            **{
                "is_success": True,
                "json.return_value": {
                    "access_token": "new_tok",
                    "expires_in": 3600,
                },
            }
        )
    )

    provider = GCalProvider(client_id="cid", client_secret="csec", redirect_uri="https://x/cb")

    with (
        patch("httpx.AsyncClient", return_value=mock_http),
        patch(
            "artemis.integrations.gcal.provider._persist_refreshed_tokens",
            new_callable=AsyncMock,
            side_effect=mock_persist,
        ),
    ):
        ok = await provider.verify(mock_integration)

    assert ok is True
    assert len(persist_called) == 1
    assert persist_called[0]["integration_id"] == 5
    assert persist_called[0]["new_access"] == "new_tok"


# ─────────────────────────────────────────────────────────────────────────────
# 4. _refresh_tokens() returns (access, refresh, expires_at) tuple
# ─────────────────────────────────────────────────────────────────────────────


async def test_gcal_refresh_tokens_returns_tuple() -> None:
    """_refresh_tokens returns the correct (access, refresh, expires_at) triple."""
    from artemis.integrations.gcal.provider import _refresh_tokens

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.json.return_value = {
        "access_token": "at_new",
        "refresh_token": "rt_new",
        "expires_in": 3600,
    }

    before = time.time()
    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _refresh_tokens("rt", "cid", "csec")

    after = time.time()
    assert result is not None
    new_access, new_refresh, new_expires = result
    assert new_access == "at_new"
    assert new_refresh == "rt_new"
    assert before + 3590 <= new_expires <= after + 3610


async def test_gcal_refresh_tokens_echoes_refresh_when_not_rotated() -> None:
    """_refresh_tokens echoes back the original refresh_token when Google omits it."""
    from artemis.integrations.gcal.provider import _refresh_tokens

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.json.return_value = {
        "access_token": "at_new",
        # no refresh_token key
        "expires_in": 3600,
    }

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        result = await _refresh_tokens("original_rt", "cid", "csec")

    assert result is not None
    _, new_refresh, _ = result
    assert new_refresh == "original_rt"  # echoed back


# ─────────────────────────────────────────────────────────────────────────────
# 5. PeopleClient._refresh() fires on_tokens_refreshed + captures rotated token
# ─────────────────────────────────────────────────────────────────────────────


async def test_people_client_refresh_calls_callback() -> None:
    """PeopleClient._refresh() fires on_tokens_refreshed with new tokens."""
    from artemis.integrations.gcal.people_client import PeopleClient

    callback_args: list[dict[str, Any]] = []

    async def on_refreshed(access_token: str, refresh_token: str, expires_at: float) -> None:
        callback_args.append({"access_token": access_token, "refresh_token": refresh_token})

    client = PeopleClient(
        access_token="old_at",
        refresh_token="old_rt",
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
        "access_token": "new_at",
        "refresh_token": "new_rt",
        "expires_in": 3600,
    }

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        await client._refresh()

    assert client._access_token == "new_at"
    assert client._refresh_token == "new_rt"
    assert len(callback_args) == 1
    assert callback_args[0]["access_token"] == "new_at"
    assert callback_args[0]["refresh_token"] == "new_rt"


async def test_people_client_refresh_no_callback_ok() -> None:
    """PeopleClient._refresh() without a callback still updates in-memory token."""
    from artemis.integrations.gcal.people_client import PeopleClient

    client = PeopleClient(
        access_token="old_at",
        refresh_token="rt",
        client_id="cid",
        client_secret="csec",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "access_token": "new_at",
        "expires_in": 3600,
    }

    mock_http = AsyncMock()
    mock_http.__aenter__ = AsyncMock(return_value=mock_http)
    mock_http.__aexit__ = AsyncMock(return_value=False)
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        await client._refresh()

    assert client._access_token == "new_at"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Gmail _resolve_gmail_client callback opens a FRESH session
# ─────────────────────────────────────────────────────────────────────────────


async def test_gmail_resolve_client_callback_opens_fresh_session() -> None:
    """_resolve_gmail_client's on_tokens_refreshed callback opens a new DB session.

    The old implementation captured the session from the async-with block, which
    was closed by the time the callback fired.  The fix opens a new session inside
    the callback via _db.SessionLocal().

    Imports in _resolve_gmail_client are lazy (inside the function body), so we
    patch them at their source modules, not at artemis.integrations.gmail.tools.X.
    """
    from artemis.integrations.gmail.tools import _resolve_gmail_client

    future_expiry = datetime.fromtimestamp(time.time() + 3600, tz=UTC)

    mock_credential = MagicMock()
    mock_credential.user_id = 1
    mock_credential.access_token = "at"
    mock_credential.refresh_token = "rt"
    mock_credential.expiry = future_expiry
    mock_credential.scope = "https://www.googleapis.com/auth/gmail.readonly"
    mock_credential.connected_email = "jon@test.com"

    mock_config = MagicMock()
    mock_config.client_id = "cid"
    mock_config.client_secret = "csec"

    upsert_called: list[dict[str, Any]] = []

    mock_new_session = AsyncMock()
    mock_new_session.commit = AsyncMock()
    mock_new_session.__aenter__ = AsyncMock(return_value=mock_new_session)
    mock_new_session.__aexit__ = AsyncMock(return_value=False)

    async def mock_upsert(
        session: Any,
        *,
        user_id: int,
        purpose: Any,
        access_token: str,
        refresh_token: Any,
        expiry: Any,
        scope: Any,
        connected_email: Any,
    ) -> Any:
        upsert_called.append({"access_token": access_token, "refresh_token": refresh_token})
        return MagicMock()

    mock_session_local = MagicMock(return_value=mock_new_session)

    with (
        # Patch at source — lazy imports inside _resolve_gmail_client pull from
        # these modules directly; patching artemis.integrations.gmail.tools.X
        # doesn't work because the name isn't bound at module level.
        patch(
            "artemis.google_docs.repository.get_google_credential",
            new_callable=AsyncMock,
            return_value=mock_credential,
        ),
        patch(
            "artemis.google_integration.google_has_any_scope",
            return_value=True,
        ),
        patch(
            "artemis.google_integration.resolve_google_oauth_client_config",
            new_callable=AsyncMock,
            return_value=mock_config,
        ),
        patch("artemis.db.SessionLocal", mock_session_local),
        patch(
            "artemis.google_docs.repository.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=mock_upsert,
        ),
    ):
        client = await _resolve_gmail_client()

    assert client is not None

    # Fire the callback — simulates what GmailClient._refresh() does on success.
    await client._on_tokens_refreshed("new_at", "new_rt", time.time() + 3600)

    # upsert_google_credential was called with the new tokens via a fresh session.
    assert len(upsert_called) == 1
    assert upsert_called[0]["access_token"] == "new_at"
    assert upsert_called[0]["refresh_token"] == "new_rt"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Gmail _on_tokens_refreshed: failure is logged and not re-raised
# ─────────────────────────────────────────────────────────────────────────────


async def test_gmail_callback_failure_is_swallowed() -> None:
    """If upsert_google_credential raises, the callback logs and does NOT re-raise."""
    from artemis.integrations.gmail.tools import _resolve_gmail_client

    future_expiry = datetime.fromtimestamp(time.time() + 3600, tz=UTC)

    mock_credential = MagicMock()
    mock_credential.user_id = 1
    mock_credential.access_token = "at"
    mock_credential.refresh_token = "rt"
    mock_credential.expiry = future_expiry
    mock_credential.scope = "https://www.googleapis.com/auth/gmail.readonly"
    mock_credential.connected_email = "jon@test.com"

    mock_config = MagicMock()
    mock_config.client_id = "cid"
    mock_config.client_secret = "csec"

    mock_new_session = AsyncMock()
    mock_new_session.commit = AsyncMock()
    mock_new_session.__aenter__ = AsyncMock(return_value=mock_new_session)
    mock_new_session.__aexit__ = AsyncMock(return_value=False)

    async def mock_upsert_raises(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("DB is down")

    mock_session_local = MagicMock(return_value=mock_new_session)

    with (
        patch(
            "artemis.google_docs.repository.get_google_credential",
            new_callable=AsyncMock,
            return_value=mock_credential,
        ),
        patch(
            "artemis.google_integration.google_has_any_scope",
            return_value=True,
        ),
        patch(
            "artemis.google_integration.resolve_google_oauth_client_config",
            new_callable=AsyncMock,
            return_value=mock_config,
        ),
        patch("artemis.db.SessionLocal", mock_session_local),
        patch(
            "artemis.google_docs.repository.upsert_google_credential",
            new_callable=AsyncMock,
            side_effect=mock_upsert_raises,
        ),
    ):
        client = await _resolve_gmail_client()

    assert client is not None

    # This must not raise — failures are swallowed and logged
    await client._on_tokens_refreshed("new_at", "new_rt", time.time() + 3600)
