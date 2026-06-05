"""Calendar router — /api/calendar.

Endpoints:
  GET    /api/calendar/overview          — today's event summary (GCal-backed)
  GET    /api/calendar/events            — events in a date range (rangeStart, rangeEnd ISO8601)
  GET    /api/calendar/event/{id}        — single event by ID
  POST   /api/calendar/event             — create event
  PATCH  /api/calendar/event/{id}        — update event
  DELETE /api/calendar/event/{id}        — delete event
  POST   /api/calendar/event/{id}/respond — RSVP (accept/decline/maybe)
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
from artemis.integrations.gcal.client import GCalAPIError, GCalClient
from artemis.integrations.gcal.types import EventDateTime
from artemis.marketing.routes._auth import require_token

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/calendar",
    tags=["calendar"],
    dependencies=[Depends(require_token)],
)


async def _get_gcal_client(session: AsyncSession) -> GCalClient:
    """Return a configured GCalClient or raise 503 if not connected."""
    rows = await repo.list_active(session, provider="gcal")
    if not rows:
        raise HTTPException(status_code=503, detail="Google Calendar not connected.")
    integration = rows[0]
    creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    return GCalClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
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
        "uid": event.id,
        "title": event.summary or "Untitled event",
        "start": event.start.date_time or event.start.date,
        "end": event.end.date_time or event.end.date,
        "description": event.description,
        "location": getattr(event, "location", None),
        "status": "scheduled",
        "attendees": [
            {"email": a.email, "responseStatus": a.response_status} for a in (event.attendees or [])
        ],
    }


@router.get("/events")
async def get_calendar_events(
    range_start: str = Query(..., alias="rangeStart", description="ISO 8601 start, inclusive."),
    range_end: str = Query(..., alias="rangeEnd", description="ISO 8601 end, exclusive."),
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
            time_min=range_start,
            time_max=range_end,
            max_results=250,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("GCal events fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar fetch failed: {exc}") from exc

    out: list[dict[str, Any]] = []
    for event in events:
        # Map to the shape the frontend renderer expects (Node-era contract):
        # uid, title, start, end, description, location, status.
        out.append(
            {
                "uid": event.id,
                "title": event.summary or "Untitled event",
                "start": event.start.date_time or event.start.date,
                "end": event.end.date_time or event.end.date,
                "description": event.description,
                "location": getattr(event, "location", None),
                "status": "scheduled",
            }
        )
    return out


# ── Mutation routes ────────────────────────────────────────────────────────────


def _event_to_dict(event: Any) -> dict[str, Any]:
    """Map a GCal Event object to the frontend wire shape."""
    return {
        "uid": event.id,
        "title": event.summary or "Untitled event",
        "start": event.start.date_time or event.start.date,
        "end": event.end.date_time or event.end.date,
        "description": event.description,
        "location": getattr(event, "location", None),
        "status": "scheduled",
        "attendees": [
            {"email": a.email, "responseStatus": a.response_status} for a in (event.attendees or [])
        ],
    }


@router.post("/event", status_code=201)
async def create_calendar_event(
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a new GCal event.

    Body fields: title (str), start (ISO 8601), end (ISO 8601),
    description (str, optional), attendees (list[str] of emails, optional),
    allDay (bool, optional — uses date instead of dateTime).
    """
    title = str(body.get("title", "")).strip() or "New Event"
    start_raw = str(body.get("start", ""))
    end_raw = str(body.get("end", ""))
    description = body.get("description")
    attendees: list[str] = body.get("attendees") or []
    all_day = bool(body.get("allDay", False))

    if not start_raw or not end_raw:
        raise HTTPException(status_code=422, detail={"error": "start and end are required"})

    if all_day:
        # GCal all-day events use date, not dateTime.
        start_dt = EventDateTime(date=start_raw[:10])
        end_dt = EventDateTime(date=end_raw[:10])
    else:
        start_dt = EventDateTime(date_time=start_raw)
        end_dt = EventDateTime(date_time=end_raw)

    try:
        client = await _get_gcal_client(session)
        event = await client.create_event(
            calendar_id="primary",
            summary=title,
            start=start_dt,
            end=end_dt,
            attendees=attendees or None,
            description=str(description) if description else None,
        )
    except HTTPException:
        raise
    except GCalAPIError as exc:
        logger.warning("GCal create_event failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Calendar create failed: {exc}") from exc
    except Exception as exc:
        logger.warning("GCal create_event failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar create failed: {exc}") from exc

    return _event_to_dict(event)


@router.patch("/event/{event_id}")
async def update_calendar_event(
    event_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """Update a GCal event. Sends a PUT (full-replace) to the GCal API.

    Accepted body fields: title, start, end, description, attendees, allDay.
    Omitted fields are preserved from the existing event.
    """
    all_day = bool(body.get("allDay", False))

    start_dt: EventDateTime | None = None
    end_dt: EventDateTime | None = None
    if "start" in body:
        s = str(body["start"])
        start_dt = EventDateTime(date=s[:10]) if all_day else EventDateTime(date_time=s)
    if "end" in body:
        e = str(body["end"])
        end_dt = EventDateTime(date=e[:10]) if all_day else EventDateTime(date_time=e)

    try:
        client = await _get_gcal_client(session)
        event = await client.update_event(
            calendar_id="primary",
            event_id=event_id,
            summary=str(body["title"]) if "title" in body else None,
            start=start_dt,
            end=end_dt,
            attendees=body.get("attendees"),
            description=str(body["description"]) if "description" in body else None,
        )
    except HTTPException:
        raise
    except GCalAPIError as exc:
        logger.warning("GCal update_event failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Calendar update failed: {exc}") from exc
    except Exception as exc:
        logger.warning("GCal update_event failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar update failed: {exc}") from exc

    return _event_to_dict(event)


@router.delete("/event/{event_id}", status_code=204)
async def delete_calendar_event(
    event_id: str,
    sendUpdates: str = Query(default="all"),  # noqa: N803
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> None:
    """Delete (cancel) a GCal event.

    sendUpdates: "all" | "externalOnly" | "none" (GCal API param).
    """
    try:
        client = await _get_gcal_client(session)
        await client.delete_event(calendar_id="primary", event_id=event_id)
    except HTTPException:
        raise
    except GCalAPIError as exc:
        logger.warning("GCal delete_event failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Calendar delete failed: {exc}") from exc
    except Exception as exc:
        logger.warning("GCal delete_event failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar delete failed: {exc}") from exc


@router.post("/event/{event_id}/respond")
async def respond_to_calendar_event(
    event_id: str,
    body: dict[str, Any],
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, Any]:
    """RSVP to a calendar event (accept / decline / tentative).

    Body: { response: "accepted" | "declined" | "tentative" }
    Implements RSVP by patching the self attendee's responseStatus via update_event.
    """
    response_str = str(body.get("response", "")).strip().lower()
    valid_responses = {"accepted", "declined", "tentative", "needsAction"}
    if response_str not in valid_responses:
        raise HTTPException(
            status_code=422,
            detail={"error": f"response must be one of: {', '.join(sorted(valid_responses))}"},
        )

    try:
        client = await _get_gcal_client(session)
        # Fetch current event to get attendee list.
        current = await client.get_event(calendar_id="primary", event_id=event_id)

        # Find the self/organizer attendee and update their responseStatus.
        # GCal RSVP is done by patching the attendees array via the events.patch API.
        # Since our client uses PUT (full replace), we update the attendees list ourselves.
        attendees_raw = current.model_dump(by_alias=True, exclude_none=True).get("attendees", [])
        updated = False
        for att in attendees_raw:
            if att.get("self"):
                att["responseStatus"] = response_str
                updated = True
                break

        if not updated and attendees_raw:
            # Fallback: mark first attendee (shouldn't happen for normal calendar owner).
            attendees_raw[0]["responseStatus"] = response_str

        # Rebuild Event body and PUT it.
        body_put: dict[str, Any] = current.model_dump(by_alias=True, exclude_none=True)
        body_put["attendees"] = attendees_raw

        data = await client._put(f"/calendars/primary/events/{event_id}", body_put)
        return {"ok": True, "eventId": event_id, "response": response_str, "event": data.get("id")}

    except HTTPException:
        raise
    except GCalAPIError as exc:
        logger.warning("GCal respond failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Calendar RSVP failed: {exc}") from exc
    except Exception as exc:
        logger.warning("GCal respond failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Calendar RSVP failed: {exc}") from exc
