"""Tool: contact_db_stub.has_contact

Stub — returns "true" for all districts in v1.
Design decision: stub returns True for priority districts until Salesforce
integration ships.

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
    name="contact_db_stub.has_contact",
    description=(
        "Check whether we have a contact at a district. "
        "STUB: returns 'true' for all districts in v1 until Salesforce integration ships."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "districtId": {
                "type": "string",
                "description": "The district ID to look up.",
            }
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning(
            "contact_db_stub.has_contact called (stub, returning true) by agent=%s",
            ctx.agent_id,
        )
        return "true"

    return (_DEF, _impl)


register_tool("contact_db_stub.has_contact", _factory)
