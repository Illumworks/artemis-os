"""Tool: campaign_brief.write

Write a campaign brief row. Only marketing.content.brief_assembler may call this.
Reuses CampaignBrief model from artemis.marketing.models.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.models import CampaignBrief
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_ALLOWED_AGENT = "marketing.content.brief_assembler"

_DEF = Tool(
    name="campaign_brief.write",
    description=(
        "Write an assembled campaign brief for a candidate. "
        "Only the brief_assembler agent may call this tool. "
        "Returns the new brief ID on success, or PERMISSION_DENIED."
    ),
    input_schema={
        "type": "object",
        "required": ["candidateId", "content"],
        "properties": {
            "candidateId": {
                "type": "integer",
                "description": "The campaign_candidates.id to attach the brief to.",
            },
            "content": {
                "type": "object",
                "description": "The assembled brief content as a JSON object.",
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        if ctx.agent_id != _ALLOWED_AGENT:
            return f"PERMISSION_DENIED: agent {ctx.agent_id!r} cannot call campaign_brief.write"
        candidate_id: int | None = arguments.get("candidateId")
        content: Any = arguments.get("content", {})
        if candidate_id is None:
            return "VALIDATION_ERROR: 'candidateId' is required"
        row = CampaignBrief(
            candidate_id=candidate_id,
            content=content,
            generated_by=ctx.agent_id,
        )
        ctx.session.add(row)
        await ctx.session.flush()
        logger.info(
            "campaign_brief.write: agent=%s brief_id=%s candidate_id=%s",
            ctx.agent_id,
            row.id,
            candidate_id,
        )
        return json.dumps({"brief_id": row.id, "status": "written"})

    return (_DEF, _impl)


register_tool("campaign_brief.write", _factory)
