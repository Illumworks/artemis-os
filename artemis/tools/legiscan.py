"""Tools: legiscan.search, legiscan.get_bill

Stubs — requires LEGISCAN_API_KEY in Connectors panel.

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
    name="legiscan.search",
    description=(
        "Search LegiScan for state legislation. "
        "STUB: requires LEGISCAN_API_KEY in the Connectors panel."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "state": {"type": "string"},
        },
    },
)

_DEF_GET_BILL = Tool(
    name="legiscan.get_bill",
    description=(
        "Retrieve a specific bill from LegiScan by bill ID. "
        "STUB: requires LEGISCAN_API_KEY in the Connectors panel."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "billId": {"type": "string"},
        },
    },
)


def _factory_search(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("legiscan.search called (stub) by agent=%s", ctx.agent_id)
        return (
            "STUB: legiscan.search not yet implemented. Set LEGISCAN_API_KEY in Connectors panel."
        )

    return (_DEF_SEARCH, _impl)


def _factory_get_bill(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("legiscan.get_bill called (stub) by agent=%s", ctx.agent_id)
        return (
            "STUB: legiscan.get_bill not yet implemented. Set LEGISCAN_API_KEY in Connectors panel."
        )

    return (_DEF_GET_BILL, _impl)


register_tool("legiscan.search", _factory_search)
register_tool("legiscan.get_bill", _factory_get_bill)
