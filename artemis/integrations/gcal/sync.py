"""Calendar cache synchronization for the personal Google account."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import resolve_gcal_config
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.gcal.auth_dead import handle_gcal_auth_dead
from artemis.integrations.gcal.client import GCalAuthDeadError, GCalClient
from artemis.integrations.gcal.models import GCalEventCache
from artemis.integrations.gcal.types import Event

logger = logging.getLogger(__name__)


async def _client_from_row(row: Any, session: AsyncSession) -> GCalClient:
    """Build a GCalClient from an integrations row, always using the DB-resolved client.

    The client_id/client_secret stored in the encrypted credentials blob may be
    stale (written when an env override was active).  We resolve the live DB client
    via resolve_gcal_config and override whatever is in the blob so the refresh
    token exchange uses the SAME client that issued the tokens (DB client 975559492379).
    """
    encrypted_credentials = row.encrypted_credentials
    creds = decrypt_credentials(bytes(encrypted_credentials))
    integration_id: int = row.id

    # Always use the DB-authoritative client credentials for refresh.
    # This prevents invalid_client 401s when the stored blob has a stale
    # client_id/secret from a previous env-override configuration.
    gcal_cfg = await resolve_gcal_config(session)
    live_client_id = gcal_cfg.client_id
    live_client_secret = gcal_cfg.client_secret

    async def _on_tokens_refreshed(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        new_creds = dict(creds)
        new_creds["access_token"] = access_token
        new_creds["refresh_token"] = refresh_token
        new_creds["expires_at"] = expires_at
        # Also persist the live client credentials so subsequent refreshes stay
        # consistent even if this callback is invoked after a client rotation.
        new_creds["client_id"] = live_client_id
        new_creds["client_secret"] = live_client_secret
        await repo.persist_refreshed_credentials(
            session,
            integration_id=integration_id,
            new_creds=new_creds,
        )

    return GCalClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=live_client_id,
        client_secret=live_client_secret,
        expires_at=float(str(creds.get("expires_at") or 0)),
        on_tokens_refreshed=_on_tokens_refreshed,
    )


def _parse_event_datetime(raw_date_time: str | None, raw_date: str | None) -> datetime | None:
    if raw_date_time:
        parsed = datetime.fromisoformat(raw_date_time)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    if raw_date:
        parsed_date = date.fromisoformat(raw_date)
        return datetime.combine(parsed_date, time.min).replace(tzinfo=UTC)
    return None


async def sync_recent_gcal_events_cache(
    session: AsyncSession,
    *,
    hours_back: int = 12,
    days_ahead: int = 14,
    calendar_id: str = "primary",
) -> int:
    """Upsert a rolling window of Google Calendar events into gcal_events_cache."""
    try:
        rows = await repo.list_active(session, provider="gcal")
    except Exception:
        logger.warning("Failed to resolve active gcal integration for cache sync", exc_info=True)
        return 0
    if not rows:
        return 0

    client = await _client_from_row(rows[0], session)
    now = datetime.now(UTC)
    window_start = now - timedelta(hours=hours_back)
    window_end = now + timedelta(days=days_ahead)
    try:
        events = await client.list_events(
            calendar_id=calendar_id,
            time_min=window_start.isoformat(),
            time_max=window_end.isoformat(),
            max_results=250,
        )
    except GCalAuthDeadError:
        logger.error(
            "sync_recent_gcal_events_cache: GCal auth dead for integration_id=%d",
            rows[0].id,
        )
        await handle_gcal_auth_dead(session, rows[0].id)
        return 0
    except Exception:
        logger.warning("Failed to sync gcal_events_cache", exc_info=True)
        return 0

    for event in events:
        await _upsert_cached_event(session, calendar_id=calendar_id, event=event, fetched_at=now)
    await session.flush()
    return len(events)


async def _upsert_cached_event(
    session: AsyncSession,
    *,
    calendar_id: str,
    event: Event,
    fetched_at: datetime,
) -> None:
    stmt = (
        pg_insert(GCalEventCache.__table__)  # type: ignore[arg-type]
        .values(
            calendar_id=calendar_id,
            event_id=event.id,
            summary=event.summary,
            start_at=_parse_event_datetime(event.start.date_time, event.start.date),
            end_at=_parse_event_datetime(event.end.date_time, event.end.date),
            attendees=[
                attendee.model_dump(by_alias=True, exclude_none=True)
                for attendee in event.attendees
            ],
            description=event.description,
            fetched_at=fetched_at,
        )
        .on_conflict_do_update(
            index_elements=[GCalEventCache.calendar_id, GCalEventCache.event_id],
            set_={
                "summary": event.summary,
                "start_at": _parse_event_datetime(event.start.date_time, event.start.date),
                "end_at": _parse_event_datetime(event.end.date_time, event.end.date),
                "attendees": [
                    attendee.model_dump(by_alias=True, exclude_none=True)
                    for attendee in event.attendees
                ],
                "description": event.description,
                "fetched_at": fetched_at,
            },
        )
    )
    await session.execute(stmt)
