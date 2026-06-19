"""Google Calendar OAuth provider — implements IntegrationProvider."""

from __future__ import annotations

import logging
import time

import httpx

from artemis.integrations import IntegrationProvider
from artemis.integrations.crypto import decrypt_credentials, encrypt_credentials
from artemis.integrations.models import Integration

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GCAL_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"


class GCalProvider(IntegrationProvider):
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    async def connect(self, code: str) -> Integration:
        """Exchange OAuth code; return an unsaved Integration (caller must flush+commit)."""
        async with httpx.AsyncClient(timeout=15) as http:
            token_resp = await http.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
        token_resp.raise_for_status()
        token_data = token_resp.json()

        access_token: str = token_data["access_token"]
        refresh_token: str = token_data.get("refresh_token", "")
        expires_in: int = int(token_data.get("expires_in", 3600))

        async with httpx.AsyncClient(timeout=10) as http:
            info_resp = await http.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        info_resp.raise_for_status()
        info = info_resp.json()
        email: str = info["email"]

        credentials = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "expires_at": time.time() + expires_in,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        encrypted = encrypt_credentials(credentials)

        return Integration(
            provider="gcal",
            workspace_id=email,
            display_name=email,
            encrypted_credentials=encrypted,
            scopes=["https://www.googleapis.com/auth/calendar"],
            status="active",
            metadata_={},
        )

    async def verify(self, integration: Integration) -> bool:
        """Ping the GCal API; refresh + persist the token if we get a 401.

        On a successful refresh we write the new credentials back to the DB so
        the refreshed token is not lost when this request ends.  We open a
        dedicated session here because verify() is called from a route handler
        that owns its own session — opening a second session for the persist
        keeps the two transactions independent and avoids double-commit issues.
        """
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        access_token = str(creds.get("access_token", ""))
        refresh_token = str(creds.get("refresh_token", ""))
        client_id = str(creds.get("client_id", ""))
        client_secret = str(creds.get("client_secret", ""))

        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.get(
                _GCAL_LIST_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if resp.status_code == 401 and refresh_token:
                refresh_result = await _refresh_tokens(refresh_token, client_id, client_secret)
                if refresh_result is not None:
                    new_access, new_refresh, new_expires_at = refresh_result
                    resp = await http.get(
                        _GCAL_LIST_URL,
                        headers={"Authorization": f"Bearer {new_access}"},
                    )
                    if resp.is_success:
                        # Persist the new tokens so subsequent requests don't 401 again.
                        await _persist_refreshed_tokens(
                            integration_id=integration.id,
                            existing_creds=creds,
                            new_access=new_access,
                            new_refresh=new_refresh,
                            new_expires_at=new_expires_at,
                        )

        return resp.is_success

    async def revoke(self, integration: Integration) -> None:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        token = str(creds.get("access_token", ""))
        if not token:
            return
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(_GOOGLE_REVOKE_URL, params={"token": token})


async def _refresh_tokens(
    refresh_token: str, client_id: str, client_secret: str
) -> tuple[str, str, float] | None:
    """POST a refresh_token grant and return (access_token, refresh_token, expires_at).

    Returns None on any failure.  The returned refresh_token is the new one
    when Google rotates it, otherwise the original is echoed back.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            resp = await http.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
        if resp.is_success:
            body = resp.json()
            new_access = str(body.get("access_token", ""))
            new_refresh = str(body.get("refresh_token") or refresh_token)
            expires_in = int(body.get("expires_in", 3600))
            new_expires_at = time.time() + expires_in
            return (new_access, new_refresh, new_expires_at)
    except Exception:
        logger.warning("GCal token refresh failed")
    return None


async def _persist_refreshed_tokens(
    *,
    integration_id: int,
    existing_creds: dict[str, object],
    new_access: str,
    new_refresh: str,
    new_expires_at: float,
) -> None:
    """Write refreshed GCal tokens back to the integrations row.

    Opens its own DB session so callers don't need to pass one in.
    Failures are logged but never re-raised — a persist failure must not
    abort the verify call that triggered it.
    """
    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo

        new_creds = dict(existing_creds)
        new_creds["access_token"] = new_access
        new_creds["refresh_token"] = new_refresh
        new_creds["expires_at"] = new_expires_at

        async with _db.SessionLocal() as session:
            await repo.persist_refreshed_credentials(
                session,
                integration_id=integration_id,
                new_creds=new_creds,
            )
            await session.commit()
    except Exception:
        logger.warning(
            "GCal verify: failed to persist refreshed tokens for integration_id=%d",
            integration_id,
            exc_info=True,
        )
