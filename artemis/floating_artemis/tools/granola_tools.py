"""Granola meeting-notes tools for Floating Artemis.

Three read tools — all Layer 2 (idempotent, no approval required):
  list_recent_meetings   — meetings in a time range
  get_meeting_transcript — full transcript for a meeting ID
  get_meeting_summary    — shorter structured summary for a meeting ID

[surface:meetings] — all tools in this module require the meetings surface.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

logger = logging.getLogger(__name__)

_SURFACE = "[surface:meetings]"


# ── Implementations ───────────────────────────────────────────────────────────


async def _list_recent_meetings(inp: dict[str, Any]) -> str:
    time_range = str(inp.get("time_range") or "last_7_days")
    valid_ranges = {"last_30_days", "last_7_days", "this_week"}
    if time_range not in valid_ranges:
        time_range = "last_7_days"
    limit = int(inp.get("limit") or 20)

    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.granola.client import GranolaClient

        async with _db.SessionLocal() as session:
            rows = await repo.list_active(session, provider="granola")

        if not rows:
            return "Granola is not connected. Ask the user to connect via Settings → Integrations."

        creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
        client = GranolaClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
            expires_at=float(str(creds.get("expires_at") or 0)),
        )
        meetings = await client.list_meetings(time_range=time_range, limit=limit)
        if not meetings:
            return f"No meetings found in range: {time_range}"
        lines = [
            f"{m.date_raw or 'unknown date'} — {m.title} (id: {m.id})"
            + (f" [participants: {', '.join(m.participants)}]" if m.participants else "")
            for m in meetings
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("list_recent_meetings failed: %s", exc)
        return f"list_recent_meetings failed: {exc}"


async def _get_meeting_transcript(inp: dict[str, Any]) -> str:
    meeting_id = str(inp.get("meeting_id") or "").strip()
    if not meeting_id:
        return "Error: meeting_id is required"

    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.granola.client import GranolaClient

        async with _db.SessionLocal() as session:
            rows = await repo.list_active(session, provider="granola")

        if not rows:
            return "Granola is not connected."

        creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
        client = GranolaClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
            expires_at=float(str(creds.get("expires_at") or 0)),
        )
        detail = await client.get_meeting(meeting_id)
        if not detail:
            return f"No transcript found for meeting {meeting_id}"
        transcript = detail.get("transcript") or json.dumps(detail)
        return transcript[:8000]  # cap for context budget
    except Exception as exc:
        logger.warning("get_meeting_transcript failed: %s", exc)
        return f"get_meeting_transcript failed: {exc}"


async def _get_meeting_summary(inp: dict[str, Any]) -> str:
    meeting_id = str(inp.get("meeting_id") or "").strip()
    if not meeting_id:
        return "Error: meeting_id is required"

    try:
        import artemis.db as _db
        from artemis.integrations import repository as repo
        from artemis.integrations.crypto import decrypt_credentials
        from artemis.integrations.granola.client import GranolaClient

        async with _db.SessionLocal() as session:
            rows = await repo.list_active(session, provider="granola")

        if not rows:
            return "Granola is not connected."

        creds = decrypt_credentials(bytes(rows[0].encrypted_credentials))
        client = GranolaClient(
            access_token=str(creds.get("access_token", "")),
            refresh_token=str(creds.get("refresh_token", "")),
            client_id=str(creds.get("client_id", "")),
            client_secret=str(creds.get("client_secret", "")),
            expires_at=float(str(creds.get("expires_at") or 0)),
        )
        detail = await client.get_meeting(meeting_id)
        if not detail:
            return f"No data found for meeting {meeting_id}"

        # Prefer structured summary fields; fall back to truncated transcript
        summary_parts: list[str] = []
        if detail.get("title"):
            summary_parts.append(f"Title: {detail['title']}")
        if detail.get("summary"):
            summary_parts.append(f"Summary: {detail['summary']}")
        elif detail.get("transcript"):
            # Return first 1000 chars as a rough summary
            summary_parts.append(f"Transcript excerpt: {str(detail['transcript'])[:1000]}")
        if detail.get("action_items"):
            summary_parts.append(f"Action items: {detail['action_items']}")
        if detail.get("attendees"):
            summary_parts.append(f"Attendees: {detail['attendees']}")

        return "\n".join(summary_parts) if summary_parts else json.dumps(detail)[:2000]
    except Exception as exc:
        logger.warning("get_meeting_summary failed: %s", exc)
        return f"get_meeting_summary failed: {exc}"


# ── Tool definitions ──────────────────────────────────────────────────────────


_LIST_RECENT_MEETINGS = Tool(
    name="list_recent_meetings",
    description=(
        f"{_SURFACE} List recent Granola meetings in a time range. "
        "Returns meeting titles, dates, IDs, and participants. "
        "time_range: last_7_days | last_30_days | this_week. Layer 2 — no approval required."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "time_range": {
                "type": "string",
                "enum": ["last_7_days", "last_30_days", "this_week"],
                "description": "Time range to fetch meetings for",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of meetings to return (default 20)",
            },
        },
    },
)

_GET_MEETING_TRANSCRIPT = Tool(
    name="get_meeting_transcript",
    description=(
        f"{_SURFACE} Get the full transcript for a Granola meeting by ID. "
        "Use list_recent_meetings first to find the meeting_id. Layer 2 — no approval required."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "meeting_id": {
                "type": "string",
                "description": "Granola meeting ID from list_recent_meetings",
            },
        },
        "required": ["meeting_id"],
    },
)

_GET_MEETING_SUMMARY = Tool(
    name="get_meeting_summary",
    description=(
        f"{_SURFACE} Get a structured summary (title, summary, action items, attendees) "
        "for a Granola meeting by ID. Layer 2 — no approval required."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "meeting_id": {
                "type": "string",
                "description": "Granola meeting ID from list_recent_meetings",
            },
        },
        "required": ["meeting_id"],
    },
)


# ── Registry registration ─────────────────────────────────────────────────────


def register_granola_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all Granola tools into the FA tool registry.

    Called from chat.py _build_tool_registry only when "meetings" surface is available.
    """
    registry.register(_LIST_RECENT_MEETINGS, _list_recent_meetings, layer=2)
    registry.register(_GET_MEETING_TRANSCRIPT, _get_meeting_transcript, layer=2)
    registry.register(_GET_MEETING_SUMMARY, _get_meeting_summary, layer=2)
