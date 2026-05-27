"""Tools: linkedin_scraper.fetch_posts, linkedin_scraper.check_profile_delta

Stubs — LinkedIn scraping requires Playwright (deferred) or API credentials.

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

_DEF_FETCH = Tool(
    name="linkedin_scraper.fetch_posts",
    description=(
        "Fetch recent LinkedIn posts for a company or person. "
        "STUB: requires Playwright or LinkedIn API credentials."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "profileUrl": {"type": "string"},
            "limit": {"type": "integer"},
        },
    },
)

_DEF_DELTA = Tool(
    name="linkedin_scraper.check_profile_delta",
    description=(
        "Check for profile changes (job title, bio) for a LinkedIn profile. "
        "STUB: requires Playwright or LinkedIn API credentials."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "profileUrl": {"type": "string"},
        },
    },
)


def _factory_fetch(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning("linkedin_scraper.fetch_posts called (stub) by agent=%s", ctx.agent_id)
        return "STUB: linkedin_scraper.fetch_posts not yet implemented. Set LINKEDIN_API_KEY in Connectors panel."

    return (_DEF_FETCH, _impl)


def _factory_delta(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        logger.warning(
            "linkedin_scraper.check_profile_delta called (stub) by agent=%s", ctx.agent_id
        )
        return "STUB: linkedin_scraper.check_profile_delta not yet implemented. Set LINKEDIN_API_KEY in Connectors panel."

    return (_DEF_DELTA, _impl)


register_tool("linkedin_scraper.fetch_posts", _factory_fetch)
register_tool("linkedin_scraper.check_profile_delta", _factory_delta)
