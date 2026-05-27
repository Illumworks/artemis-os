"""District lookup tool — STUB.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.

There is no ``districts`` table in the schema yet. This stub returns a
deterministic "unknown district" shape so the qualifier brief_composer can call
``districts.get`` without failing the chain, and logs a WARNING so the call is
visible. When a district roster is imported (future work — a ``districts`` table
seeded from the Node app's district data), replace this stub with a real lookup
that returns enrollment / region / contact metadata.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_MARKETING_PREFIX = "marketing."

_DEF = Tool(
    name="districts.get",
    description=(
        "Look up a school district by ID. NOTE: district roster import is not yet "
        "implemented, so this currently returns {known: false} for every district. "
        "Returns a JSON object {district_id, known}. Any marketing agent may call this."
    ),
    input_schema={
        "type": "object",
        "required": ["districtId"],
        "properties": {
            "districtId": {
                "type": "string",
                "description": "The district identifier to look up.",
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if not ctx.agent_id.startswith(_MARKETING_PREFIX):
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot look up districts"
        district_id = arguments.get("districtId")
        if not isinstance(district_id, str) or not district_id:
            return "VALIDATION_ERROR: 'districtId' is required and must be a non-empty string"
        logger.warning(
            "districts.get: STUB — no district roster imported; returning known=false for %r",
            district_id,
        )
        return json.dumps({"district_id": district_id, "known": False})

    return (_DEF, _impl)


register_tool("districts.get", _factory)
