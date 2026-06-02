"""Tool: unresolved_signals.write

Write a malformed-signal row for later triage.
The unresolved_signals table does not exist in marketing/models.py (checked) —
this tool is a stub returning a placeholder string.

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
    name="unresolved_signals.write",
    description=(
        "Write a malformed or unresolvable signal row for later triage. "
        "STUB: unresolved_signals table not yet present in this schema version."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "Why the signal could not be resolved."},
            "payload": {"type": "object", "description": "The original signal payload."},
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning(
            "unresolved_signals.write called (stub) by agent=%s — table not yet present",
            ctx.agent_id,
        )
        return "STUB: unresolved_signals table not present"

    return (_DEF, _impl)


register_tool("unresolved_signals.write", _factory)
