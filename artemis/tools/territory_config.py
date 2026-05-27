"""Tools: territory_config.get_priority_states, territory_config.get_watch_keywords

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.marketing.josh_spec import parse_spec
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_DEF_PRIORITY_STATES = Tool(
    name="territory_config.get_priority_states",
    description=(
        "Return the list of priority states from the campaign-signal spec. No arguments required."
    ),
    input_schema={"type": "object", "properties": {}},
)

_DEF_WATCH_KEYWORDS = Tool(
    name="territory_config.get_watch_keywords",
    description=(
        "Return watch keywords from the campaign-type mappings. "
        "If campaignType is provided, return keywords for that type only; "
        "otherwise return all keywords."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "campaignType": {
                "type": "string",
                "description": "Campaign type slug, e.g. 'obc'. Omit for all.",
            }
        },
    },
)


def _factory_priority_states(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        spec = parse_spec()
        return json.dumps(list(spec.territory_config.priority_states))

    return (_DEF_PRIORITY_STATES, _impl)


def _factory_watch_keywords(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        spec = parse_spec()
        campaign_type: str | None = arguments.get("campaignType")
        result: dict[str, list[str]] = {}
        for mapping in spec.campaign_type_mappings:
            if campaign_type and mapping.campaign_type != campaign_type:
                continue
            result[mapping.campaign_type] = list(mapping.watch_keywords)
        return json.dumps(result)

    return (_DEF_WATCH_KEYWORDS, _impl)


register_tool("territory_config.get_priority_states", _factory_priority_states)
register_tool("territory_config.get_watch_keywords", _factory_watch_keywords)
