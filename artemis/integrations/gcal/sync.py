"""Calendar cache synchronization for the personal Google account."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.gcal.client import GCalClient
from artemis.integrations.gcal.models import GCalEventCache
from artemis.integrations.gcal.types import Event

logger = logging.getLogger(__name__)


def _client_from_row(row: object) -> GCalClient:
    encrypted_credentials = row.encrypted_credentials  # type: ignore[attr-defined]
    creds = decrypt_credentials(bytes(encrypted_credentials))
    return GCalClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
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

    client = _client_from_row(rows[0])
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
