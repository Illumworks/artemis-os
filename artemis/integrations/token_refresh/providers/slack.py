"""Slack token refresher.

Slack token rotation is opt-in. Apps with `token_rotation_enabled` issue
`xoxe-` access tokens with `xoxe-1-…` refresh tokens via `oauth.v2.access`
with `grant_type=refresh_token`. Workspaces using legacy non-rotating `xoxb-`
bot tokens have no refresh_token at all — those tokens don't expire, and the
scheduler should skip them forever via NO_REFRESH_TOKEN.

Today's symptom of Slack `invalid_auth` is most likely a *different* failure
mode (revoked install or scope drift), not expiry — that case will surface as
REFRESH_TOKEN_EXPIRED here and route through the same `needs_reauth` flow.
"""

from __future__ import annotations

import logging
import time

import httpx

from artemis.integrations.slack.provider import _SLACK_OAUTH_URL
from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult

logger = logging.getLogger(__name__)


class SlackTokenRefresher:
    """Refresh a Slack rotating access token via the refresh_token grant."""

    provider = "slack"

    async def refresh(self, creds: dict[str, object]) -> RefreshResult:
        refresh_token = str(creds.get("refresh_token") or "")
        client_id = str(creds.get("client_id") or "")
        client_secret = str(creds.get("client_secret") or "")

        if not refresh_token:
            # Non-rotating xoxb- bot token: no refresh possible (and not needed).
            return RefreshResult(outcome=RefreshOutcome.NO_REFRESH_TOKEN)

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if client_id:
            data["client_id"] = client_id
        if client_secret:
            data["client_secret"] = client_secret

        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(_SLACK_OAUTH_URL, data=data)
        except httpx.HTTPError as exc:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"network error: {exc}",
            )

        if not resp.is_success:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"slack {resp.status_code}: {resp.text[:200]}",
            )

        try:
            body = resp.json()
        except ValueError as exc:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"slack response not JSON: {exc}",
            )

        if not body.get("ok"):
            err = str(body.get("error") or "")
            # Slack signals dead refresh tokens through these error codes.
            if err in ("invalid_refresh_token", "invalid_grant_type", "token_revoked"):
                return RefreshResult(
                    outcome=RefreshOutcome.REFRESH_TOKEN_EXPIRED,
                    error=f"slack {err}",
                )
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"slack {err or 'unknown'}",
            )

        new_access = body.get("access_token")
        if not new_access:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error="slack response missing access_token",
            )

        # Rotating Slack apps return both a fresh access_token and a fresh
        # refresh_token; expires_in is the access_token lifetime in seconds.
        expires_in = int(body.get("expires_in", 3600))
        new_expires_at = time.time() + expires_in
        new_refresh = body.get("refresh_token") or refresh_token

        new_creds = dict(creds)
        new_creds["access_token"] = str(new_access)
        new_creds["refresh_token"] = str(new_refresh)
        new_creds["expires_at"] = new_expires_at

        return RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds=new_creds)
