"""Tool: grants_gov.search

Stub — requires Grants.gov API integration.

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
    name="grants_gov.search",
    description=("Search Grants.gov for federal grant opportunities. STUB: not yet implemented."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "agency": {"type": "string"},
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("grants_gov.search called (stub) by agent=%s", ctx.agent_id)
        return "STUB: grants_gov.search not yet implemented. Set GRANTS_GOV_API_KEY in Connectors panel."

    return (_DEF, _impl)


register_tool("grants_gov.search", _factory)
