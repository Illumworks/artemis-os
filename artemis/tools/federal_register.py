"""Tool: federal_register.search

Stub — Federal Register API integration not yet implemented.

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
    name="federal_register.search",
    description=(
        "Search the Federal Register for regulations and notices. STUB: not yet implemented."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "agencies": {"type": "array", "items": {"type": "string"}},
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("federal_register.search called (stub) by agent=%s", ctx.agent_id)
        return "STUB: federal_register.search not yet implemented. Set FEDERAL_REGISTER_API_KEY in Connectors panel."

    return (_DEF, _impl)


register_tool("federal_register.search", _factory)
