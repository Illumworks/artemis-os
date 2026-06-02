"""Tools: reason_codes.get_allowlist, reason_codes.lookup

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF_ALLOWLIST = Tool(
    name="reason_codes.get_allowlist",
    description=(
        "Return the reason codes this scout is allowed to emit, with descriptions "
        "and default urgency. No arguments required — the calling scout's identity "
        "is inferred from the agent context."
    ),
    input_schema={"type": "object", "properties": {}},
)

_DEF_LOOKUP = Tool(
    name="reason_codes.lookup",
    description=(
        "Return the full ReasonCodeSpec for a single reason code: description, "
        "what_scout_looks_for, and default_urgency. Returns an error string if the "
        "code is not in the spec."
    ),
    input_schema={
        "type": "object",
        "required": ["code"],
        "properties": {"code": {"type": "string", "description": "The reason code to look up."}},
    },
)


def _factory_allowlist(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        slug = ctx.agent_id.rsplit(".", 1)[-1]
        spec = parse_spec()
        codes = reason_codes_for_scout(spec, slug)
        return json.dumps(
            [
                {
                    "code": rc.code,
                    "description": rc.description,
                    "default_urgency": rc.default_urgency,
                }
                for rc in codes
            ]
        )

    return (_DEF_ALLOWLIST, _impl)


def _factory_lookup(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        code: str = arguments.get("code", "")
        if not code:
            return "ERROR: 'code' argument is required"
        spec = parse_spec()
        for rc in spec.reason_codes:
            if rc.code == code:
                return json.dumps(
                    {
                        "code": rc.code,
                        "description": rc.description,
                        "what_scout_looks_for": rc.what_scout_looks_for,
                        "default_urgency": rc.default_urgency,
                    }
                )
        return f"ERROR: reason code {code!r} not found in spec"

    return (_DEF_LOOKUP, _impl)


register_tool("reason_codes.get_allowlist", _factory_allowlist)
register_tool("reason_codes.lookup", _factory_lookup)
