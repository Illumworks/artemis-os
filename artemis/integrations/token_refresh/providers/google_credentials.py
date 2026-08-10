"""Proactive token refresh for google_credentials rows (personal + marketing).

The standard TokenRefresher sweep only covers ``integrations`` rows.  Google
credentials for Docs/Sheets/Gmail are stored in a separate ``google_credentials``
table keyed by (user_id, purpose) — no ``integrations`` row exists for them, so
they are never swept.

This module provides ``refresh_google_credentials_tick``, called from
``run_refresh_tick`` alongside the integrations sweep.  It iterates ALL rows in
``google_credentials`` (both purpose=personal and purpose=marketing) and refreshes
any that are expiring within the leeway window, using the DB-authoritative OAuth
client (resolve_gcal_config — same resolver that the connect flow uses).
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.google_docs.models import GoogleCredential
from artemis.google_docs.repository import upsert_google_credential
from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_gcal_config
from artemis.integrations.token_refresh.base import RefreshOutcome

logger = logging.getLogger(__name__)

_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Leeway and cooldown mirrors the main scheduler constants — keep in sync.
_REFRESH_LEEWAY_MINUTES = 30
_COOLDOWN_MINUTES = 10


def _extract_google_error(resp: httpx.Response) -> str:
    """Return a short diagnostic from a Google error response body."""
    try:
        body = resp.json()
        code = str(body.get("error") or "")
        desc = str(body.get("error_description") or "")
        if code and desc:
            return f"{code}: {desc}"
        return code or resp.text[:200]
    except Exception:
        return resp.text[:200]


async def refresh_google_credentials_tick(session: object) -> None:
    """Sweep all google_credentials rows and refresh tokens expiring within leeway.

    Uses the DB-authoritative OAuth client (resolve_gcal_config) so that refresh
    always uses the same client that issued the tokens — prevents invalid_client 401.

    Persists refreshed tokens directly via upsert_google_credential.

    This function is intentionally tolerant of individual-row failures:
    each row is wrapped in try/except so a bad row never aborts the sweep.
    """
    db: AsyncSession = session  # type: ignore[assignment]

    # Resolve the DB-authoritative OAuth client once per tick.
    try:
        gcal_cfg = await resolve_gcal_config(db)
    except MissingProviderConfigError:
        logger.warning(
            "google_credentials_tick: gcal client config missing — cannot refresh google_credentials"
        )
        return

    client_id = gcal_cfg.client_id
    client_secret = gcal_cfg.client_secret

    now = datetime.now(UTC)
    leeway_cutoff = now + timedelta(minutes=_REFRESH_LEEWAY_MINUTES)
    cooldown_floor = now - timedelta(minutes=_COOLDOWN_MINUTES)

    result = await db.execute(select(GoogleCredential))
    rows: list[GoogleCredential] = list(result.scalars().all())

    for row in rows:
        try:
            await _maybe_refresh_row(
                db=db,
                row=row,
                client_id=client_id,
                client_secret=client_secret,
                leeway_cutoff=leeway_cutoff,
                cooldown_floor=cooldown_floor,
            )
        except Exception:
            logger.exception(
                "google_credentials_tick: error refreshing google_credential id=%d "
                "(user_id=%d, purpose=%r)",
                row.id,
                row.user_id,
                row.purpose,
            )


async def _maybe_refresh_row(
    *,
    db: AsyncSession,
    row: GoogleCredential,
    client_id: str,
    client_secret: str,
    leeway_cutoff: datetime,
    cooldown_floor: datetime,
) -> None:
    """Refresh a single GoogleCredential row if it needs it."""
    # Skip if no refresh token — cannot refresh without one.
    if not row.refresh_token:
        return

    # Expiry is a tz-aware datetime in google_credentials.
    expiry = row.expiry
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)

    # Skip if still healthy.
    if expiry > leeway_cutoff:
        return

    # Cooldown: skip if updated_at is within the cooldown window.
    # updated_at is set by upsert_google_credential on every write.
    updated_at = row.updated_at
    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if updated_at > cooldown_floor:
            return

    outcome, payload = await _do_google_refresh(
        refresh_token=row.refresh_token,
        client_id=client_id,
        client_secret=client_secret,
    )

    if outcome == RefreshOutcome.REFRESHED and isinstance(payload, dict):
        new_refresh = payload.get("refresh_token") or row.refresh_token
        await upsert_google_credential(
            db,
            user_id=row.user_id,
            purpose=row.purpose,  # type: ignore[arg-type]
            access_token=payload["access_token"],
            refresh_token=new_refresh,
            expiry=payload["expiry"],
            scope=row.scope,
            connected_email=row.connected_email,
        )
        logger.info(
            "google_credentials_tick: refreshed google_credential id=%d (user_id=%d, purpose=%r)",
            row.id,
            row.user_id,
            row.purpose,
        )
    elif outcome == RefreshOutcome.REFRESH_TOKEN_EXPIRED:
        logger.error(
            "google_credentials_tick: refresh token EXPIRED for google_credential id=%d "
            "(user_id=%d, purpose=%r) — reauth required. error=%r",
            row.id,
            row.user_id,
            row.purpose,
            payload,
        )
    elif outcome == RefreshOutcome.TRANSIENT_FAILURE:
        logger.warning(
            "google_credentials_tick: transient failure refreshing google_credential id=%d "
            "(user_id=%d, purpose=%r). error=%r",
            row.id,
            row.user_id,
            row.purpose,
            payload,
        )


async def _do_google_refresh(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> tuple[RefreshOutcome, object]:
    """POST a refresh_token grant to Google.

    Returns (REFRESHED, new_token_dict) on success, or (outcome, error_str) on failure.
    new_token_dict has keys: access_token (str), refresh_token (str | None), expiry (datetime).
    """
    try:
        async with httpx.AsyncClient(timeout=20) as http:
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
        return RefreshOutcome.TRANSIENT_FAILURE, f"network error: {exc}"

    if resp.status_code == 400:
        google_error = _extract_google_error(resp)
        return RefreshOutcome.REFRESH_TOKEN_EXPIRED, f"google 400: {google_error}"

    if not resp.is_success:
        google_error = _extract_google_error(resp)
        logger.error(
            "google_credentials_tick: Google token endpoint returned %d: %s",
            resp.status_code,
            google_error,
        )
        return RefreshOutcome.TRANSIENT_FAILURE, f"google {resp.status_code}: {google_error}"

    try:
        body = resp.json()
    except ValueError as exc:
        return RefreshOutcome.TRANSIENT_FAILURE, f"non-JSON response: {exc}"

    new_access = str(body.get("access_token") or "")
    if not new_access:
        return RefreshOutcome.TRANSIENT_FAILURE, "response missing access_token"

    expires_in = int(body.get("expires_in", 3600))
    new_expiry = datetime.fromtimestamp(time.time() + expires_in, UTC)
    new_refresh = body.get("refresh_token") or None  # None → caller keeps existing

    return RefreshOutcome.REFRESHED, {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "expiry": new_expiry,
    }
