"""Google Calendar OAuth provider — implements IntegrationProvider."""

from __future__ import annotations

import logging

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
                new_token = await _refresh_access_token(refresh_token, client_id, client_secret)
                if new_token:
                    resp = await http.get(
                        _GCAL_LIST_URL,
                        headers={"Authorization": f"Bearer {new_token}"},
                    )

        return resp.is_success

    async def revoke(self, integration: Integration) -> None:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        token = str(creds.get("access_token", ""))
        if not token:
            return
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(_GOOGLE_REVOKE_URL, params={"token": token})


async def _refresh_access_token(
    refresh_token: str, client_id: str, client_secret: str
) -> str | None:
    """Return a fresh access token, or None if refresh fails."""
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
            return str(resp.json().get("access_token", ""))
    except Exception:
        logger.warning("GCal token refresh failed")
    return None
