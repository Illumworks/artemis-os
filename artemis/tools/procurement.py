"""Tool: procurement_portal.fetch

Stub — statewide procurement portal scraping not yet implemented.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF = Tool(
    name="procurement_portal.fetch",
    description=(
        "Fetch procurement opportunities from a statewide procurement portal. "
        "STUB: not yet implemented."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("procurement_portal.fetch called (stub) by agent=%s", ctx.agent_id)
        return "STUB: procurement_portal.fetch not yet implemented. Set PROCUREMENT_PORTAL_URL in Connectors panel."

    return (_DEF, _impl)


register_tool("procurement_portal.fetch", _factory)
