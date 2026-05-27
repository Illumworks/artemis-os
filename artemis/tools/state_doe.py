"""Tool: state_doe.fetch

Fetches state Department of Education RSS items for a given state.
Reuses artemis.scouts.state_doe.sources.fetch_doe_rss.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.state_doe.sources import fetch_doe_rss
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="state_doe.fetch",
    description=(
        "Fetch recent news items from a state Department of Education RSS feed. "
        "Returns items as JSON [{title, link, published, summary}]. "
        "Returns [] if the state is not configured or on any error."
    ),
    input_schema={
        "type": "object",
        "required": ["state"],
        "properties": {
            "state": {
                "type": "string",
                "description": "2-letter US state code, e.g. 'FL'.",
            }
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        state: str = arguments.get("state", "").upper()
        if not state:
            return json.dumps([])
        try:
            async with ScoutHttpClient(timeout=20.0) as http:
                items = await fetch_doe_rss(state, http)
        except Exception as exc:
            logger.warning("state_doe.fetch(%s): error — %s", state, exc)
            return json.dumps([])
        return json.dumps(items)

    return (_DEF, _impl)


register_tool("state_doe.fetch", _factory)
