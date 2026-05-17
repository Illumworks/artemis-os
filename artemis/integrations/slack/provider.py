"""Slack OAuth provider — implements IntegrationProvider."""

from __future__ import annotations

import logging

import httpx

from artemis.integrations import IntegrationProvider
from artemis.integrations.crypto import decrypt_credentials, encrypt_credentials
from artemis.integrations.models import Integration

logger = logging.getLogger(__name__)

_SLACK_OAUTH_URL = "https://slack.com/api/oauth.v2.access"
_SLACK_AUTH_TEST_URL = "https://slack.com/api/auth.test"
_SLACK_REVOKE_URL = "https://slack.com/api/auth.revoke"


class SlackProvider(IntegrationProvider):
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    async def connect(self, code: str) -> Integration:
        """Exchange OAuth code; return an unsaved Integration (caller must flush+commit)."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _SLACK_OAUTH_URL,
                data={
                    "code": code,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                },
            )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise ValueError(f"Slack OAuth error: {data.get('error', 'unknown')}")

        token = data["access_token"]
        team_id = data["team"]["id"]
        team_name = data["team"]["name"]
        bot_user_id = data.get("bot_user_id")
        scopes = data.get("scope", "").split(",") if data.get("scope") else []

        credentials = {"access_token": token, "token_type": "bot"}
        encrypted = encrypt_credentials(credentials)

        row = Integration(
            provider="slack",
            workspace_id=team_id,
            display_name=team_name,
            bot_user_id=bot_user_id,
            encrypted_credentials=encrypted,
            scopes=scopes,
            status="active",
            metadata_={},
        )
        return row

    async def verify(self, integration: Integration) -> bool:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        token = creds["access_token"]
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                _SLACK_AUTH_TEST_URL,
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            return False
        data = resp.json()
        return bool(data.get("ok"))

    async def revoke(self, integration: Integration) -> None:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        token = creds["access_token"]
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                _SLACK_REVOKE_URL,
                headers={"Authorization": f"Bearer {token}"},
            )
