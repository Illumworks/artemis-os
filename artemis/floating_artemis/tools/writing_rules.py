"""Writing Rules tools for Floating Artemis.

Authority layers:
  1: list_writing_rules
  3: propose_writing_rule

[surface:writing-rules] — gated by writing-rules surface availability.
"""

from __future__ import annotations

import json
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

_SURFACE = "[surface:writing-rules]"


async def _list_writing_rules(inp: dict[str, Any]) -> str:
    profile_id = inp.get("profile_id")
    limit = int(inp.get("limit", 30))
    try:
        import artemis.db as _db
        from artemis.writing_rules import repository as repo

        async with _db.SessionLocal() as session:
            rules = await repo.list_rules(
                session,
                profile_id=int(profile_id) if profile_id else None,
                limit=limit,
            )
        if not rules:
            return "No writing rules found."
        lines = [f"[{r.id}] [{r.rule_type}] {r.title}: {r.description or ''}" for r in rules]
        return "\n".join(lines)
    except Exception as exc:
        return f"list_writing_rules failed: {exc}"


async def _propose_writing_rule(inp: dict[str, Any]) -> str:
    title = inp.get("title", "")
    rule_type = inp.get("rule_type", "style")
    description = inp.get("description", "")
    profile_id = inp.get("profile_id")
    if not title:
        return "Error: title is required"
    proposal = {
        "type": "writing_rule_proposal",
        "title": title,
        "rule_type": rule_type,
        "description": description,
        "profile_id": profile_id,
    }
    return f"Writing rule proposal (pending confirmation):\n{json.dumps(proposal, indent=2)}"


LIST_WRITING_RULES = Tool(
    name="list_writing_rules",
    description=f"List writing rules from the rules library. {_SURFACE} [layer:1]",
    input_schema={
        "type": "object",
        "properties": {
            "profile_id": {"type": "integer", "description": "Filter by profile ID"},
            "limit": {"type": "integer", "default": 30},
        },
        "required": [],
    },
)

PROPOSE_WRITING_RULE = Tool(
    name="propose_writing_rule",
    description=f"Propose a new writing rule (requires operator confirmation). {_SURFACE} [layer:3]",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "rule_type": {"type": "string", "default": "style"},
            "description": {"type": "string"},
            "profile_id": {"type": "integer"},
        },
        "required": ["title"],
    },
)


def register_writing_rules_tools(registry: AuthorizedToolRegistry) -> None:
    registry.register(LIST_WRITING_RULES, _list_writing_rules, layer=1)
    registry.register(PROPOSE_WRITING_RULE, _propose_writing_rule, layer=3)
