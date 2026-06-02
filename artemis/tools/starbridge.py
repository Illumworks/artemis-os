"""Tools: starbridge.search, starbridge.get_document

Stubs — requires Starbridge API credentials (STARBRIDGE_API_KEY) in the .env file.

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

_DEF_SEARCH = Tool(
    name="starbridge.search",
    description=(
        "Search Starbridge for procurement and funding documents. "
        "STUB: requires Starbridge API credentials (STARBRIDGE_API_KEY) in the .env file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "state": {"type": "string"},
        },
    },
)

_DEF_GET_DOC = Tool(
    name="starbridge.get_document",
    description=(
        "Retrieve a specific document from Starbridge by ID. "
        "STUB: requires Starbridge API credentials (STARBRIDGE_API_KEY) in the .env file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "documentId": {"type": "string"},
        },
    },
)


def _factory_search(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("starbridge.search called (stub) by agent=%s", ctx.agent_id)
        return "STUB: starbridge.search not yet implemented. Set STARBRIDGE_API_KEY in the .env file."

    return (_DEF_SEARCH, _impl)


def _factory_get_doc(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("starbridge.get_document called (stub) by agent=%s", ctx.agent_id)
        return "STUB: starbridge.get_document not yet implemented. Set STARBRIDGE_API_KEY in the .env file."

    return (_DEF_GET_DOC, _impl)


register_tool("starbridge.search", _factory_search)
register_tool("starbridge.get_document", _factory_get_doc)
