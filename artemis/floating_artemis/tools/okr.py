"""OKR tools for Floating Artemis.

Authority layers:
  1: list_okr_objectives, complete_okr_checkin
  3: update_okr_kr  — propose→confirm; OKR writes MUST NOT happen without Jon's explicit approval.

[surface:okr] — gated by okr surface availability.
"""

from __future__ import annotations

from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_SURFACE = "[surface:okr]"


async def _list_okr_objectives(inp: dict[str, Any]) -> str:
    limit = int(inp.get("limit", 20))
    try:
        import artemis.db as _db
        from artemis.okr import repository as repo

        async with _db.SessionLocal() as session:
            objectives = await repo.list_objectives(session)
        if not objectives:
            return "No OKR objectives found."
        lines = []
        for obj in objectives[:limit]:
            lines.append(f"[{obj.id}] {obj.title} (progress: {obj.progress}%)")
        return "\n".join(lines)
    except Exception as exc:
        return f"list_okr_objectives failed: {exc}"


async def _update_okr_kr(inp: dict[str, Any]) -> str:
    kr_id = inp.get("kr_id")
    progress = inp.get("progress")
    if not kr_id:
        return "Error: kr_id is required"
    try:
        import artemis.db as _db
        from artemis.okr import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_key_result(session, int(kr_id), current_value=progress)
            await session.commit()
        return f"KR {kr_id} updated: current_value={progress}"
    except Exception as exc:
        return f"update_okr_kr failed: {exc}"


async def _complete_okr_checkin(inp: dict[str, Any]) -> str:
    """Mark the live OKR check-in breadcrumb as completed for the current speaker.

    Called by the agent when the operator signals they are done with the
    weekly reconciliation exchange (topic change, "that's all", "thanks", etc.).
    Stamping completed_at stops the reconcile context from re-injecting on
    subsequent DMs.  This is a benign bookkeeping action — no OKR data changes.
    The Monday TTL remains as a passive backstop even if this is never called.
    """
    speaker_id: str | None = inp.get("speaker_id") or None
    if not speaker_id:
        return "complete_okr_checkin: speaker_id is required"
    try:
        import artemis.db as _db
        from artemis.proactivity.repository import (
            complete_okr_checkin_breadcrumb,
            get_live_okr_checkin_breadcrumb,
        )

        async with _db.SessionLocal() as session:
            crumb = await get_live_okr_checkin_breadcrumb(session, speaker_id)
            if crumb is None:
                return "No live check-in breadcrumb found — nothing to complete."
            await complete_okr_checkin_breadcrumb(session, crumb.id)
            await session.commit()
        return f"OKR check-in breadcrumb {crumb.id} marked complete for speaker {speaker_id}."
    except Exception as exc:
        return f"complete_okr_checkin failed: {exc}"


LIST_OKR_OBJECTIVES = Tool(
    name="list_okr_objectives",
    description=f"List OKR objectives with their key results. {_SURFACE} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
        "required": [],
    },
)

UPDATE_OKR_KR = Tool(
    name="update_okr_kr",
    description=(
        f"Update the current progress value for a key result. {_SURFACE} [layer:3] "
        "REQUIRES Jon's explicit confirmation before executing — never auto-invoked."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kr_id": {"type": "integer"},
            "progress": {"type": "number", "description": "New current value"},
        },
        "required": ["kr_id", "progress"],
    },
)

COMPLETE_OKR_CHECKIN = Tool(
    name="complete_okr_checkin",
    description=(
        f"Close out the active OKR check-in reconciliation session for the current speaker. "
        f"Call this when the operator changes topic OR signals they are done "
        f"(e.g. 'that\\'s all', 'thanks', 'nothing else'). "
        f"Stamps completed_at on the live breadcrumb so the reconcile context "
        f"stops injecting on subsequent DMs. No OKR data is modified. {_SURFACE} [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "speaker_id": {
                "type": "string",
                "description": "Slack user ID of the speaker (e.g. U01ABCDEF)",
            }
        },
        "required": ["speaker_id"],
    },
)


def register_okr_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(LIST_OKR_OBJECTIVES, _list_okr_objectives, layer=1)
    registry.register(UPDATE_OKR_KR, _update_okr_kr, layer=3)
    registry.register(COMPLETE_OKR_CHECKIN, _complete_okr_checkin, layer=1)
