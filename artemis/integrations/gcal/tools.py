"""Google Calendar tools for the Floating Artemis tool registry.

Five tools — two read-only (layer 2) and three side-effect (layer 3).
Each implementation resolves the active GCal integration at call time.
"""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

# ── Implementations ───────────────────────────────────────────────────────────


def _gcal_client_from_creds(creds: dict[str, object], integration_id: int | None = None) -> Any:
    from artemis.integrations.gcal.client import GCalClient

    async def _on_tokens_refreshed(
        access_token: str, refresh_token: str, expires_at: float
    ) -> None:
        if integration_id is None:
            return
        import artemis.db as _db
        from artemis.integrations import repository as repo

        new_creds = dict(creds)
        new_creds["access_token"] = access_token
        new_creds["refresh_token"] = refresh_token
        new_creds["expires_at"] = expires_at
        async with _db.SessionLocal() as session:
            try:
                await repo.persist_refreshed_credentials(
                    session,
                    integration_id=integration_id,
                    new_creds=new_creds,
                )
                await session.commit()
            except Exception:
                import logging

                logging.getLogger(__name__).debug(
                    "gcal tools: persist_refreshed_credentials failed", exc_info=True
                )

    return GCalClient(
        access_token=str(creds.get("access_token", "")),
        refresh_token=str(creds.get("refresh_token", "")),
        client_id=str(creds.get("client_id", "")),
        client_secret=str(creds.get("client_secret", "")),
        expires_at=float(str(creds.get("expires_at") or 0)),
        on_tokens_refreshed=_on_tokens_refreshed if integration_id is not None else None,
    )


async def _resolve_gcal_creds() -> tuple[dict[str, object], int] | None:
    """Return (creds_dict, integration_id) for the active GCal integration, or None."""
    import artemis.db as _db
    from artemis.integrations import repository as repo
    from artemis.integrations.crypto import decrypt_credentials

    async with _db.SessionLocal() as session:
        integrations = await repo.list_active(session, provider="gcal")
    if not integrations:
        return None
    row = integrations[0]
    return decrypt_credentials(bytes(row.encrypted_credentials)), row.id


async def _list_calendars(inp: dict[str, Any]) -> str:
    try:
        result = await _resolve_gcal_creds()
        if result is None:
            return "No active Google Calendar integration found"
        creds, integration_id = result
        client = _gcal_client_from_creds(creds, integration_id)
        calendars = await client.list_calendars()
        return json.dumps([c.model_dump() for c in calendars])
    except Exception as exc:
        return f"list_calendars failed: {exc}"


async def _list_events(inp: dict[str, Any]) -> str:
    calendar_id: str = inp.get("calendar_id", "primary")
    time_min: str = inp.get("time_min", "")
    time_max: str = inp.get("time_max", "")
    max_results: int = int(inp.get("max_results", 50))
    if not time_min or not time_max:
        return "Error: time_min and time_max are required (RFC 3339)"
    try:
        result = await _resolve_gcal_creds()
        if result is None:
            return "No active Google Calendar integration found"
        creds, integration_id = result
        client = _gcal_client_from_creds(creds, integration_id)
        events = await client.list_events(calendar_id, time_min, time_max, max_results)
        return json.dumps([e.model_dump() for e in events])
    except Exception as exc:
        return f"list_events failed: {exc}"


async def _create_event(inp: dict[str, Any]) -> str:
    calendar_id: str = inp.get("calendar_id", "primary")
    summary: str = inp.get("summary", "")
    start_str: str = inp.get("start", "")
    end_str: str = inp.get("end", "")
    attendees: list[str] | None = inp.get("attendees")
    description: str | None = inp.get("description")
    if not summary or not start_str or not end_str:
        return "Error: summary, start, and end are required"
    try:
        from artemis.integrations.gcal.types import EventDateTime

        result = await _resolve_gcal_creds()
        if result is None:
            return "No active Google Calendar integration found"
        creds, integration_id = result
        client = _gcal_client_from_creds(creds, integration_id)
        event = await client.create_event(
            calendar_id=calendar_id,
            summary=summary,
            start=EventDateTime(date_time=start_str),
            end=EventDateTime(date_time=end_str),
            attendees=attendees,
            description=description,
        )
        return json.dumps(event.model_dump())
    except Exception as exc:
        return f"create_event failed: {exc}"


async def _update_event(inp: dict[str, Any]) -> str:
    calendar_id: str = inp.get("calendar_id", "primary")
    event_id: str = inp.get("event_id", "")
    if not event_id:
        return "Error: event_id is required"
    summary: str | None = inp.get("summary")
    start_str: str | None = inp.get("start")
    end_str: str | None = inp.get("end")
    attendees: list[str] | None = inp.get("attendees")
    description: str | None = inp.get("description")
    try:
        from artemis.integrations.gcal.types import EventDateTime

        result = await _resolve_gcal_creds()
        if result is None:
            return "No active Google Calendar integration found"
        creds, integration_id = result
        client = _gcal_client_from_creds(creds, integration_id)
        event = await client.update_event(
            calendar_id=calendar_id,
            event_id=event_id,
            summary=summary,
            start=EventDateTime(date_time=start_str) if start_str else None,
            end=EventDateTime(date_time=end_str) if end_str else None,
            attendees=attendees,
            description=description,
        )
        return json.dumps(event.model_dump())
    except Exception as exc:
        return f"update_event failed: {exc}"


async def _delete_event(inp: dict[str, Any]) -> str:
    calendar_id: str = inp.get("calendar_id", "primary")
    event_id: str = inp.get("event_id", "")
    if not event_id:
        return "Error: event_id is required"
    try:
        result = await _resolve_gcal_creds()
        if result is None:
            return "No active Google Calendar integration found"
        creds, integration_id = result
        client = _gcal_client_from_creds(creds, integration_id)
        await client.delete_event(calendar_id, event_id)
        return json.dumps({"ok": True, "deleted": event_id})
    except Exception as exc:
        return f"delete_event failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────

LIST_CALENDARS = Tool(
    name="list_calendars",
    description="List the user's Google Calendars. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)

LIST_EVENTS = Tool(
    name="list_events",
    description="List calendar events within a time window. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "calendar_id": {
                "type": "string",
                "description": "Calendar ID (default: 'primary')",
                "default": "primary",
            },
            "time_min": {
                "type": "string",
                "description": "Start of window, RFC 3339 (e.g. 2024-01-15T00:00:00Z)",
            },
            "time_max": {
                "type": "string",
                "description": "End of window, RFC 3339",
            },
            "max_results": {"type": "integer", "default": 50},
        },
        "required": ["time_min", "time_max"],
    },
)

CREATE_EVENT = Tool(
    name="create_event",
    description="Create a new Google Calendar event. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "default": "primary"},
            "summary": {"type": "string", "description": "Event title"},
            "start": {"type": "string", "description": "Start datetime, RFC 3339"},
            "end": {"type": "string", "description": "End datetime, RFC 3339"},
            "attendees": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses",
            },
            "description": {"type": "string"},
        },
        "required": ["summary", "start", "end"],
    },
)

UPDATE_EVENT = Tool(
    name="update_event",
    description="Update an existing Google Calendar event. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "default": "primary"},
            "event_id": {"type": "string", "description": "Google Calendar event ID"},
            "summary": {"type": "string"},
            "start": {"type": "string", "description": "New start datetime, RFC 3339"},
            "end": {"type": "string", "description": "New end datetime, RFC 3339"},
            "attendees": {"type": "array", "items": {"type": "string"}},
            "description": {"type": "string"},
        },
        "required": ["event_id"],
    },
)

DELETE_EVENT = Tool(
    name="delete_event",
    description="Delete a Google Calendar event. Requires operator confirmation. [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "calendar_id": {"type": "string", "default": "primary"},
            "event_id": {"type": "string", "description": "Google Calendar event ID"},
        },
        "required": ["event_id"],
    },
)


def register_gcal_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all Google Calendar tools into the provided registry."""
    registry.register(LIST_CALENDARS, _list_calendars, layer=2)
    registry.register(LIST_EVENTS, _list_events, layer=2)
    registry.register(CREATE_EVENT, _create_event, layer=3)
    registry.register(UPDATE_EVENT, _update_event, layer=3)
    registry.register(DELETE_EVENT, _delete_event, layer=3)
