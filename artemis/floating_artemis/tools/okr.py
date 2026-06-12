"""OKR tools for Floating Artemis.

Authority layers:
  1: list_okr_objectives, complete_okr_checkin
  3: update_okr_kr   — propose→confirm; single KR write, gated.
  3: update_okr_krs  — propose→confirm; batch KR writes, one "go" applies all.

OKR writes MUST NOT happen without Jon's explicit approval.

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
    """Single KR update with activity log.  Layer-3: only executes after operator 'go'."""
    kr_id = inp.get("kr_id")
    progress = inp.get("progress")
    basis: str = str(inp.get("basis") or "").strip()
    if not kr_id:
        return "Error: kr_id is required"
    try:
        import artemis.db as _db
        from artemis.okr import repository as repo

        async with _db.SessionLocal() as session:
            await repo.update_key_result(session, int(kr_id), prog=progress)
            activity_text = "updated via check-in, approved by Jon" + (
                f" — basis: {basis}" if basis else ""
            )
            await repo.create_activity(
                session,
                kr_id=int(kr_id),
                text=activity_text,
                raw_text=basis or None,
            )
            await session.commit()
        return f"KR {kr_id} updated: prog={progress}"
    except Exception as exc:
        return f"update_okr_kr failed: {exc}"


async def _update_okr_krs(inp: dict[str, Any]) -> str:
    """Batch KR update.  Layer-3: suspends on first call; on 'go' applies ALL.

    Input: ``{"updates": [{"kr_id": N, "progress": V, "basis": "..."}, ...]}``

    Rules enforced here (defence-in-depth; the tool schema is the first gate):
    - Any item missing ``kr_id`` or ``progress`` is skipped and noted.
    - Any item with an empty/blank ``basis`` is skipped (no fabricated updates).
    - Valid items are written atomically: KR row updated + activity entry logged.
    """
    raw_updates: list[Any] = inp.get("updates") or []
    if not raw_updates:
        return "Error: updates list is required and must not be empty"

    applied: list[str] = []
    skipped: list[str] = []

    try:
        import artemis.db as _db
        from artemis.okr import repository as repo

        async with _db.SessionLocal() as session:
            for item in raw_updates:
                if not isinstance(item, dict):
                    skipped.append(f"non-dict item ignored: {item!r}")
                    continue
                kr_id = item.get("kr_id")
                progress = item.get("progress")
                basis: str = str(item.get("basis") or "").strip()

                if not kr_id:
                    skipped.append("item missing kr_id — skipped")
                    continue
                if progress is None:
                    skipped.append(f"KR {kr_id}: missing progress — skipped")
                    continue
                if not basis:
                    skipped.append(
                        f"KR {kr_id}: empty/ungrounded basis — skipped (no fabricated updates)"
                    )
                    continue

                await repo.update_key_result(session, int(kr_id), prog=progress)
                activity_text = f"updated via Friday check-in, approved by Jon — basis: {basis}"
                await repo.create_activity(
                    session,
                    kr_id=int(kr_id),
                    text=activity_text,
                    raw_text=basis,
                )
                applied.append(f"KR {kr_id} → prog={progress}")

            await session.commit()
    except Exception as exc:
        return f"update_okr_krs failed: {exc}"

    parts: list[str] = []
    if applied:
        parts.append("Applied: " + ", ".join(applied))
    if skipped:
        parts.append("Skipped: " + "; ".join(skipped))
    if not applied and not skipped:
        parts.append("No updates applied.")
    return " | ".join(parts) if parts else "No updates applied."


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
        f"Update the current progress value for a single key result. {_SURFACE} [layer:3] "
        "REQUIRES Jon's explicit confirmation before executing — never auto-invoked. "
        "For check-in word-dumps that map to multiple KRs, prefer update_okr_krs (batch)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kr_id": {"type": "integer"},
            "progress": {"type": "number", "description": "New current progress value"},
            "basis": {
                "type": "string",
                "description": "Operator's own words justifying this update (required — no fabrication)",
            },
        },
        "required": ["kr_id", "progress", "basis"],
    },
)

UPDATE_OKR_KRS = Tool(
    name="update_okr_krs",
    description=(
        f"Batch-update progress for multiple key results in a single operator confirmation. "
        f"{_SURFACE} [layer:3] "
        "REQUIRES Jon's explicit 'go' before executing — suspends after the first call with a "
        "single proposal listing all KR changes and their bases. On 'go' ALL updates apply at "
        "once; on 'cancel' NONE apply. Use this (not repeated update_okr_kr calls) when a "
        "check-in word-dump maps to multiple KRs so the operator sees one proposal and says "
        "go once. Each update MUST carry the operator's own cited words as basis — empty basis "
        "items are dropped automatically."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "updates": {
                "type": "array",
                "description": "List of KR updates to apply atomically after operator confirmation.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kr_id": {"type": "integer", "description": "ID of the key result"},
                        "progress": {
                            "type": "number",
                            "description": "New current progress value",
                        },
                        "basis": {
                            "type": "string",
                            "description": (
                                "Operator's own words justifying this update. "
                                "REQUIRED — items with empty basis are skipped."
                            ),
                        },
                    },
                    "required": ["kr_id", "progress", "basis"],
                },
                "minItems": 1,
            }
        },
        "required": ["updates"],
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
    registry.register(UPDATE_OKR_KRS, _update_okr_krs, layer=3)
    registry.register(COMPLETE_OKR_CHECKIN, _complete_okr_checkin, layer=1)
