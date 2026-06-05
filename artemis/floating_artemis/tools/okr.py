"""OKR tools for Floating Artemis.

Authority layers:
  1: list_okr_objectives
  2: update_okr_kr

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
    description=f"Update the current progress value for a key result. {_SURFACE} [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "kr_id": {"type": "integer"},
            "progress": {"type": "number", "description": "New current value"},
        },
        "required": ["kr_id", "progress"],
    },
)


def register_okr_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(LIST_OKR_OBJECTIVES, _list_okr_objectives, layer=1)
    registry.register(UPDATE_OKR_KR, _update_okr_kr, layer=2)
