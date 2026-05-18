"""Calendar router — /api/calendar.

Endpoints:
  GET  /api/calendar/overview  — today's event summary (GCal-backed)
  GET  /api/calendar/events    — events in a date range (rangeStart, rangeEnd ISO8601)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.gcal.client import GCalClient
from artemis.marketing.routes._auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/calendar",
    tags=["calendar"],
    dependencies=[Depends(require_token)],
)


def _format_start_label(dt_str: str) -> str:
    """Format an ISO datetime string as '2:00 PM' style."""
    dt = datetime.fromisoformat(dt_str)
    return dt.strftime("%-I:%M %p")


@router.get("/overview")
async def get_calendar_overview(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return today's calendar summary.

    Returns not_connected if GCal is not linked or if the API call fails.
    """
    rows = await repo.list_active(session, provider="gcal")
    if not rows:
        return {"status": "not_connected", "provider": "gcal"}

    integration = rows[0]
    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        client = GCalClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
        )

        now_utc = datetime.now(UTC)
        today_start = datetime.combine(now_utc.date(), time.min).replace(tzinfo=UTC)
        today_end = datetime.combine(now_utc.date(), time(23, 59, 59)).replace(tzinfo=UTC)

        events = await client.list_events(
            calendar_id="primary",
            time_min=today_start.isoformat(),
            time_max=today_end.isoformat(),
        )
    except Exception:
        logger.warning("GCal API call failed; returning not_connected", exc_info=True)
        return {"status": "not_connected", "provider": "gcal"}

    meetings_count = len(events)

    # Find the next upcoming event (start time >= now)
    next_event: dict[str, str] | None = None
    for event in events:
        start_dt_str = event.start.date_time
        if start_dt_str is None:
            # All-day event — skip for "next event" purposes
            continue
        try:
            start_dt = datetime.fromisoformat(start_dt_str)
            # Make timezone-aware if needed
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=UTC)
            if start_dt >= now_utc:
                next_event = {
                    "startLabel": _format_start_label(start_dt_str),
                    "title": event.summary or "(No title)",
                }
                break
        except (ValueError, AttributeError):
            continue

    return {
        "status": "ready",
        "today": {"meetingsCount": meetings_count},
        "nextEvent": next_event,
    }


@router.get("/event/{event_id}")
async def get_calendar_event(
    event_id: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return one GCal event by ID."""
    rows = await repo.list_active(session, provider="gcal")
    if not rows:
        raise HTTPException(status_code=503, detail="Google Calendar not connected.")
    integration = rows[0]
    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        client = GCalClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
        )
        event = await client.get_event(calendar_id="primary", event_id=event_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("GCal event fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar fetch failed: {exc}") from exc

    return {
        "id": event.id,
        "summary": event.summary,
        "start": event.start.date_time or event.start.date,
        "end": event.end.date_time or event.end.date,
        "description": event.description,
        "attendees": [
            {"email": a.email, "responseStatus": a.response_status}
            for a in (event.attendees or [])
        ],
    }


@router.get("/events")
async def get_calendar_events(
    rangeStart: str = Query(..., description="ISO 8601 start of range, inclusive."),
    rangeEnd: str = Query(..., description="ISO 8601 end of range, exclusive."),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List GCal events in a date range. 503 if GCal not connected."""
    rows = await repo.list_active(session, provider="gcal")
    if not rows:
        raise HTTPException(status_code=503, detail="Google Calendar not connected.")
    integration = rows[0]
    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
        client = GCalClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
        )
        events = await client.list_events(
            calendar_id="primary",
            time_min=rangeStart,
            time_max=rangeEnd,
            max_results=250,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("GCal events fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar fetch failed: {exc}") from exc

    out: list[dict[str, Any]] = []
    for event in events:
        out.append(
            {
                "id": event.id,
                "summary": event.summary,
                "start": event.start.date_time or event.start.date,
                "end": event.end.date_time or event.end.date,
                "description": event.description,
            }
        )
    return out
