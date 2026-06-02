"""Google Calendar token refresher — same shape as Granola, hits _GOOGLE_TOKEN_URL."""

from __future__ import annotations

import logging
import time

import httpx

from artemis.integrations.gcal.provider import _GOOGLE_TOKEN_URL
from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult

logger = logging.getLogger(__name__)


class GCalTokenRefresher:
    """Refresh a Google OAuth access token via the refresh_token grant."""

    provider = "gcal"

    async def refresh(self, creds: dict[str, object]) -> RefreshResult:
        refresh_token = str(creds.get("refresh_token") or "")
        client_id = str(creds.get("client_id") or "")
        client_secret = str(creds.get("client_secret") or "")

        if not refresh_token or not client_id or not client_secret:
            return RefreshResult(outcome=RefreshOutcome.NO_REFRESH_TOKEN)

        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    _GOOGLE_TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
        except httpx.HTTPError as exc:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"network error: {exc}",
            )

        # Google returns 400 with {"error":"invalid_grant"} when the refresh
        # token is revoked or the user has changed password / removed access.
        if resp.status_code == 400:
            return RefreshResult(
                outcome=RefreshOutcome.REFRESH_TOKEN_EXPIRED,
                error=f"google 400: {resp.text[:200]}",
            )
        if not resp.is_success:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"google {resp.status_code}: {resp.text[:200]}",
            )

        try:
            body = resp.json()
        except ValueError as exc:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"google response not JSON: {exc}",
            )

        new_access = body.get("access_token")
        if not new_access:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error="google response missing access_token",
            )

        # Google's refresh_token grant rarely rotates the refresh token; reuse
        # the stored one when the response omits it.
        new_refresh = body.get("refresh_token") or refresh_token
        expires_in = int(body.get("expires_in", 3600))
        new_expires_at = time.time() + expires_in

        new_creds = dict(creds)
        new_creds["access_token"] = str(new_access)
        new_creds["refresh_token"] = str(new_refresh)
        new_creds["expires_at"] = new_expires_at

        return RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds=new_creds)
