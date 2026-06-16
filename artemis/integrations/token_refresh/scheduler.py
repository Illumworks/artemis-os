"""APScheduler wiring for proactive OAuth token refresh (J10e).

In-process AsyncIOScheduler started on app startup and stopped on shutdown.
Fires `run_refresh_tick()` every 15 minutes.

Cadence rationale: 15-minute cadence × 30-minute leeway means a token will
always be refreshed at least 15 minutes before it would otherwise expire,
well before any user-facing request needs it. Sibling pattern to
`artemis/meetings/scheduler.py` (J6d); see briefs/j10e-oauth-token-refresh.md.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

import artemis.db as _db
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_gcal_config
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.models import Integration
from artemis.integrations.token_refresh.base import RefreshOutcome
from artemis.integrations.token_refresh.providers import REFRESHERS
from artemis.integrations.token_refresh.providers.google_credentials import (
    refresh_google_credentials_tick,
)

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None

CADENCE_MINUTES = 15
REFRESH_LEEWAY_MINUTES = 30
COOLDOWN_MINUTES = 10


def get_token_refresh_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler()
    return _scheduler


def start_token_refresh_scheduler() -> None:
    """Start the scheduler. Called from FastAPI lifespan startup."""
    scheduler = get_token_refresh_scheduler()
    scheduler.add_job(
        run_refresh_tick,
        trigger=IntervalTrigger(minutes=CADENCE_MINUTES),
        id="token_refresh",
        replace_existing=True,
        max_instances=1,  # never overlap ticks
        misfire_grace_time=60,
    )
    if not scheduler.running:
        scheduler.start()
        logger.info(
            "Token refresh scheduler started (cadence=%d min, leeway=%d min, cooldown=%d min)",
            CADENCE_MINUTES,
            REFRESH_LEEWAY_MINUTES,
            COOLDOWN_MINUTES,
        )


def stop_token_refresh_scheduler() -> None:
    """Stop the scheduler. Called from FastAPI lifespan shutdown."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Token refresh scheduler stopped")
    _scheduler = None


# ── Per-row decision + dispatch ──────────────────────────────────────────────


def _expires_at(creds: dict[str, object]) -> float | None:
    """Return the float expires_at from a creds dict, or None if absent/unusable."""
    raw = creds.get("expires_at")
    if raw is None:
        return None
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


async def _process_integration(session: object, integration: Integration, now: datetime) -> None:
    """Decide whether to refresh; on dispatch, persist the outcome.

    `session` is typed `object` to keep the call site simple — the SQLAlchemy
    AsyncSession is treated as opaque here and only handed to repo functions.
    """
    # Skip rows we have no refresher for (e.g. jira basic-auth).
    refresher = REFRESHERS.get(integration.provider)
    if refresher is None:
        return

    # Cooldown guard: don't retry the same row within COOLDOWN_MINUTES.
    if integration.last_refresh_attempt_at is not None:
        last = integration.last_refresh_attempt_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if now - last < timedelta(minutes=COOLDOWN_MINUTES):
            return

    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    except Exception:
        logger.warning(
            "token_refresh: could not decrypt creds for integration_id=%d", integration.id
        )
        return

    expires_at = _expires_at(creds)
    if expires_at is None:
        # No expires_at means non-expiring or unknown lifetime — skip.
        return

    # Skip if still healthy (more than the leeway window left).
    if datetime.fromtimestamp(expires_at, UTC) - now > timedelta(minutes=REFRESH_LEEWAY_MINUTES):
        return

    # For Google/GCal integrations: override the client_id/secret in the creds
    # blob with the DB-authoritative values before calling the refresher.
    # The stored blob may have a stale client from a previous env-override
    # configuration.  Refresh MUST use the same client that issued the tokens
    # (the DB client) to avoid 401 invalid_client from Google.
    if integration.provider == "gcal":
        try:
            gcal_cfg = await resolve_gcal_config(session)  # type: ignore[arg-type]
            creds = dict(creds)
            creds["client_id"] = gcal_cfg.client_id
            creds["client_secret"] = gcal_cfg.client_secret
        except MissingProviderConfigError:
            logger.warning(
                "token_refresh: could not resolve gcal client config for integration_id=%d"
                " — skipping refresh",
                integration.id,
            )
            return

    result = await refresher.refresh(creds)

    new_expires_at: float | None = None
    if result.outcome == RefreshOutcome.REFRESHED and result.new_creds is not None:
        new_expires_at = _expires_at(result.new_creds)
        await repo.persist_refreshed_credentials(
            session,  # type: ignore[arg-type]
            integration_id=integration.id,
            new_creds=result.new_creds,
        )
    elif result.outcome == RefreshOutcome.REFRESH_TOKEN_EXPIRED:
        # For GCal, capture the pre-mark status so we can decide whether to DM
        # after marking (avoids a second get_by_id inside handle_gcal_auth_dead
        # that would see the row already in needs_reauth state).
        _gcal_was_active = (
            integration.provider == "gcal" and integration.status != "needs_reauth"
        )
        await repo.mark_needs_reauth(session, integration.id)  # type: ignore[arg-type]
        if _gcal_was_active:
            # First time this integration is marked dead — send the owner DM once.
            try:
                from artemis.integrations.gcal.auth_dead import _send_owner_dm

                await _send_owner_dm(
                    session,  # type: ignore[arg-type]
                )
            except Exception:
                logger.exception(
                    "token_refresh: gcal_auth_dead DM failed for integration_id=%d",
                    integration.id,
                )
    elif result.outcome == RefreshOutcome.TRANSIENT_FAILURE:
        await repo.mark_refresh_attempted(session, integration.id)  # type: ignore[arg-type]
    # NO_REFRESH_TOKEN / STILL_VALID: no-op (skip without bumping the cooldown).

    logger.info(
        "token_refresh_tick",
        extra={
            "integration_id": integration.id,
            "provider": integration.provider,
            "outcome": result.outcome.value,
            "new_expires_at": new_expires_at,
            "error": result.error,
        },
    )


async def run_refresh_tick() -> None:
    """Scheduler entry point: sweep active integrations and refresh ageing tokens."""
    async with _db.SessionLocal() as session:
        try:
            now = datetime.now(UTC)
            integrations = await repo.list_active(session)
            for integration in integrations:
                try:
                    await _process_integration(session, integration, now)
                except Exception:
                    logger.exception(
                        "token_refresh: per-row failure (integration_id=%d, provider=%s)",
                        integration.id,
                        integration.provider,
                    )

            # Also sweep google_credentials rows (personal + marketing).
            # These have no integrations row and are never covered by the loop above.
            # Failures inside are per-row and do not abort the commit.
            try:
                await refresh_google_credentials_tick(session)
            except Exception:
                logger.exception("google_credentials_tick: sweep failed")

            await session.commit()
        except Exception:
            logger.exception("Token refresh tick failed")
            await session.rollback()
