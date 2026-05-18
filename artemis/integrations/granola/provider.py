"""Granola integration provider.

Implements IntegrationProvider with two connect modes:
  - local-state: reads tokens from the Granola desktop app's supabase.json
  - oauth: exchanges an authorization code for tokens via mcp-auth.granola.ai

connect() is overloaded via keyword arguments; the base ABC only requires
connect(code: str) but Granola's local-state path passes code="" and
access_token/refresh_token/expires_at instead.
"""

from __future__ import annotations

import logging
import time

import httpx

from artemis.integrations import IntegrationProvider
from artemis.integrations.crypto import decrypt_credentials, encrypt_credentials
from artemis.integrations.granola.client import (
    GRANOLA_TOKEN_ENDPOINT,
    GranolaAPIError,
    GranolaClient,
)
from artemis.integrations.models import Integration

logger = logging.getLogger(__name__)

_GRANOLA_REVOKE_URL = "https://mcp-auth.granola.ai/oauth2/revoke"
_GRANOLA_RESOURCE = "https://mcp.granola.ai/mcp"


class GranolaProvider(IntegrationProvider):
    """Granola OAuth provider (also handles local-state token injection)."""

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        redirect_uri: str = "",
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    # ── IntegrationProvider ABC ───────────────────────────────────────────────

    async def connect(self, code: str) -> Integration:
        """OAuth code exchange. For local-state connect, use connect_local()."""
        async with httpx.AsyncClient(timeout=15) as http:
            data: dict[str, str] = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self._redirect_uri,
                "client_id": self._client_id,
                "resource": _GRANOLA_RESOURCE,
            }
            if self._client_secret:
                data["client_secret"] = self._client_secret

            # code_verifier is passed by the route after PKCE exchange
            resp = await http.post(
                GRANOLA_TOKEN_ENDPOINT,
                data=data,
                headers={"Accept": "application/json"},
            )

        resp.raise_for_status()
        body = resp.json()
        access_token: str = body.get("access_token", "")
        if not access_token:
            raise ValueError("Granola OAuth: no access_token in token response")

        refresh_token: str = body.get("refresh_token", "")
        expires_in: int = int(body.get("expires_in", 3600))
        expires_at = time.time() + expires_in

        client = GranolaClient(access_token=access_token, refresh_token=refresh_token)
        account_info = await client.get_account_info()
        email = account_info.get("email") or account_info.get("userEmail") or "granola-user"
        display_name = account_info.get("name") or account_info.get("displayName") or str(email)

        credentials = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "auth_mode": "oauth",
        }
        encrypted = encrypt_credentials(credentials)

        return Integration(
            provider="granola",
            workspace_id=str(email),
            display_name=str(display_name),
            encrypted_credentials=encrypted,
            scopes=["openid", "profile", "email", "offline_access"],
            status="active",
            metadata_={"auth_mode": "oauth"},
        )

    async def connect_local(
        self,
        *,
        access_token: str,
        display_name: str = "Granola (local)",
        workspace_id: str = "granola-local",
    ) -> Integration:
        """Create an integration from a locally-sourced desktop-app token.

        Verifies the token is valid via get_account_info before persisting.
        """
        client = GranolaClient(access_token=access_token)
        account_info = await client.get_account_info()
        email = account_info.get("email") or account_info.get("userEmail") or workspace_id
        display_name = account_info.get("name") or account_info.get("displayName") or str(email)

        credentials = {
            "access_token": access_token,
            "refresh_token": "",
            "expires_at": 0.0,  # local tokens don't expire predictably
            "client_id": "",
            "client_secret": "",
            "auth_mode": "local",
        }
        encrypted = encrypt_credentials(credentials)

        return Integration(
            provider="granola",
            workspace_id=str(email),
            display_name=str(display_name),
            encrypted_credentials=encrypted,
            scopes=[],
            status="active",
            metadata_={"auth_mode": "local"},
        )

    async def verify(self, integration: Integration) -> bool:
        """Ping get_account_info. Auto-refresh expired OAuth tokens."""
        try:
            client = _client_from_integration(integration)
            await client.get_account_info()
            return True
        except GranolaAPIError as exc:
            if exc.status == 401:
                return False
            logger.warning("Granola verify failed: %s", exc)
            return False
        except Exception:
            logger.warning("Granola verify error", exc_info=True)
            return False

    async def revoke(self, integration: Integration) -> None:
        """Attempt provider-side revoke; swallow errors (mark revoked locally)."""
        try:
            creds = decrypt_credentials(bytes(integration.encrypted_credentials))
            token = str(creds.get("access_token", ""))
            client_id = str(creds.get("client_id", ""))
            if token:
                async with httpx.AsyncClient(timeout=10) as http:
                    await http.post(
                        _GRANOLA_REVOKE_URL,
                        data={"token": token, "client_id": client_id or "artemis"},
                        headers={"Accept": "application/json"},
                    )
        except Exception:
            logger.debug("Granola revoke request failed (non-fatal)", exc_info=True)


def _client_from_integration(integration: Integration) -> GranolaClient:
    """Build a GranolaClient from a persisted Integration row."""
    creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    return GranolaClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
        expires_at=float(str(creds.get("expires_at") or 0)),
    )
