"""Granola token refresher — ports `_refresh_token_exchange` to a standalone class."""

from __future__ import annotations

import logging
import time

import httpx

from artemis.integrations.granola.client import GRANOLA_TOKEN_ENDPOINT
from artemis.integrations.token_refresh.base import RefreshOutcome, RefreshResult

logger = logging.getLogger(__name__)


class GranolaTokenRefresher:
    """Refresh a Granola OAuth access token via the refresh_token grant."""

    provider = "granola"

    async def refresh(self, creds: dict[str, object]) -> RefreshResult:
        refresh_token = str(creds.get("refresh_token") or "")
        client_id = str(creds.get("client_id") or "")
        client_secret = str(creds.get("client_secret") or "")

        if not refresh_token or not client_id:
            return RefreshResult(outcome=RefreshOutcome.NO_REFRESH_TOKEN)

        data: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        if client_secret:
            data["client_secret"] = client_secret

        try:
            async with httpx.AsyncClient(timeout=15) as http:
                resp = await http.post(
                    GRANOLA_TOKEN_ENDPOINT,
                    data=data,
                    headers={"Accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"network error: {exc}",
            )

        if resp.status_code == 400:
            # invalid_grant or invalid_request — refresh_token is dead.
            return RefreshResult(
                outcome=RefreshOutcome.REFRESH_TOKEN_EXPIRED,
                error=f"granola 400: {resp.text[:200]}",
            )
        if not resp.is_success:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"granola {resp.status_code}: {resp.text[:200]}",
            )

        try:
            body = resp.json()
        except ValueError as exc:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error=f"granola response not JSON: {exc}",
            )

        new_access = body.get("access_token")
        if not new_access:
            return RefreshResult(
                outcome=RefreshOutcome.TRANSIENT_FAILURE,
                error="granola response missing access_token",
            )

        expires_in = int(body.get("expires_in", 3600))
        new_expires_at = time.time() + expires_in
        new_refresh = body.get("refresh_token") or refresh_token

        new_creds = dict(creds)
        new_creds["access_token"] = str(new_access)
        new_creds["refresh_token"] = str(new_refresh)
        new_creds["expires_at"] = new_expires_at

        return RefreshResult(outcome=RefreshOutcome.REFRESHED, new_creds=new_creds)
