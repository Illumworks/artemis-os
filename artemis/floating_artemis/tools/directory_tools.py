"""Directory resolution tool for Floating Artemis.

``resolve_person`` — read-only (Layer 1): map a person's NAME (or email) to one
or more candidate emails from the company directory. Available to ALL agents;
it only reads the directory cache.

CIRCULAR-IMPORT RULE: the directory resolver is imported INSIDE the tool body,
not at module top, mirroring argus_tools.py. A module-level import that pulls in
the providers/LLM stack would crash app boot.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

logger = logging.getLogger(__name__)


RESOLVE_PERSON = Tool(
    name="resolve_person",
    description=(
        "Map a person's NAME (e.g. 'Angela', 'Julie K', 'Greg Shrader') or email "
        "to candidate company email addresses from the directory. "
        "Returns a JSON list of {email, full_name, confidence}, highest confidence "
        "first. Use this before scheduling or emailing someone when you only have a "
        "name. Read-only. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The person's name to resolve (e.g. 'Julie K').",
            },
            "query": {
                "type": "string",
                "description": "Alias for 'name' — a name or email to resolve.",
            },
        },
    },
)


async def _resolve_person(inp: dict[str, Any]) -> str:
    query = str(inp.get("name") or inp.get("query") or "").strip()
    if not query:
        return "Error: 'name' (or 'query') is required"

    try:
        import artemis.db as _db
        from artemis.directory.resolver import resolve_people

        async with _db.SessionLocal() as session:
            matches = await resolve_people(query, session, limit=5)

        payload = [
            {
                "email": m.email,
                "full_name": m.full_name,
                "confidence": round(m.confidence, 3),
            }
            for m in matches
        ]
        return json.dumps(payload)
    except Exception as exc:
        logger.warning("resolve_person failed for query=%r: %s", query, exc)
        return f"resolve_person failed: {exc}"


def register_directory_tools(registry: AuthorizedToolRegistry) -> None:
    """Register the read-only directory tool. Available to all agents (Layer 1)."""
    registry.register(RESOLVE_PERSON, _resolve_person, layer=1)
