"""Tool: board_minutes.fetch

Fetches board meeting minutes/agendas for a district using BoardDocs.
Reuses artemis.scouts.board_minutes.client.fetch_boarddocs.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.board_minutes.client import fetch_boarddocs
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="board_minutes.fetch",
    description=(
        "Fetch board meeting minutes and agendas for a district from BoardDocs. "
        "Returns items as JSON [{title, date, source_url, text, speaker_attribution}]. "
        "Returns [] if the district has no boarddocs_url configured or on any error."
    ),
    input_schema={
        "type": "object",
        "required": ["district"],
        "properties": {
            "district": {
                "type": "object",
                "description": "District config dict with optional 'boarddocs_url' key.",
                "properties": {
                    "district_id": {"type": "string"},
                    "boarddocs_url": {"type": "string"},
                },
            }
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        district: dict[str, Any] = arguments.get("district") or {}
        if not district.get("boarddocs_url"):
            return json.dumps([])
        try:
            async with ScoutHttpClient(timeout=30.0) as http:
                items = await fetch_boarddocs(district, http)
        except Exception as exc:
            logger.warning("board_minutes.fetch: error — %s", exc)
            return json.dumps([])
        # Strip large text field to avoid blowing context window
        trimmed = [
            {
                "title": it.get("title", ""),
                "date": it.get("date", ""),
                "source_url": it.get("source_url", ""),
                "text": (it.get("text", ""))[:2000],
                "speaker_attribution": it.get("speaker_attribution"),
            }
            for it in items
        ]
        return json.dumps(trimmed)

    return (_DEF, _impl)


register_tool("board_minutes.fetch", _factory)
