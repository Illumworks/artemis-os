"""Calendar router — /api/calendar.

Endpoints:
  GET  /api/calendar/overview  — today's event summary (GCal-backed)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from typing import Any

from fastapi import APIRouter, Depends
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
