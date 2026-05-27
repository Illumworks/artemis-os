"""Tool: news_api.search

Google News RSS search — no API key required.
Optional enrichment via newsapi.org if NEWS_API_KEY is set.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_GOOGLE_NEWS_BASE = "https://news.google.com/rss/search"
_MAX_ITEMS = 25

_DEF = Tool(
    name="news_api.search",
    description=(
        "Search Google News RSS for recent articles matching a query. "
        "Returns up to 25 items as JSON [{title, link, published, source}]. "
        "Returns [] on any error (graceful empty)."
    ),
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query string."},
            "state": {
                "type": "string",
                "description": "Optional 2-letter US state code to append to query.",
            },
        },
    },
)


def _parse_google_news_rss(xml_text: str) -> list[dict[str, Any]]:
    """Parse Google News RSS XML into item dicts."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("news_api.search: malformed RSS XML — %s", exc)
        return []

    channel = root.find("channel")
    if channel is None:
        channel = root

    items: list[dict[str, Any]] = []
    for item_el in channel.findall("item"):
        title_el = item_el.find("title")
        link_el = item_el.find("link")
        pub_el = item_el.find("pubDate")
        source_el = item_el.find("source")

        title = (title_el.text or "").strip() if title_el is not None else ""
        link = (link_el.text or "").strip() if link_el is not None else ""
        published = (pub_el.text or "").strip() if pub_el is not None else ""
        source = (source_el.text or "").strip() if source_el is not None else ""

        items.append({"title": title, "link": link, "published": published, "source": source})
        if len(items) >= _MAX_ITEMS:
            break
    return items


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        query: str = arguments.get("query", "")
        if not query:
            return json.dumps([])

        state: str = arguments.get("state", "")
        if state:
            query = f"{query} {state}"

        encoded = urllib.parse.quote_plus(query)
        url = f"{_GOOGLE_NEWS_BASE}?q={encoded}&hl=en-US&gl=US&ceid=US:en"

        try:
            async with ScoutHttpClient(timeout=20.0) as http:
                resp = await http.get(url)
            if resp.status_code != 200:
                logger.warning("news_api.search: HTTP %d from Google News RSS", resp.status_code)
                return json.dumps([])
            items = _parse_google_news_rss(resp.text)
        except Exception as exc:
            logger.warning("news_api.search: error — %s", exc)
            return json.dumps([])

        # Optional newsapi.org enrichment — only if key is set
        news_api_key = os.getenv("NEWS_API_KEY", "")
        if news_api_key and not items:
            try:
                from artemis.scouts.regional_news.client import fetch_news_articles

                async with ScoutHttpClient(timeout=20.0) as http:
                    extra = await fetch_news_articles(
                        arguments.get("query", ""), http, api_key=news_api_key
                    )
                items = [
                    {
                        "title": a.get("title", ""),
                        "link": a.get("url", ""),
                        "published": a.get("published_at", ""),
                        "source": a.get("source_name", ""),
                    }
                    for a in extra[:_MAX_ITEMS]
                ]
            except Exception as exc:
                logger.warning("news_api.search: newsapi enrichment error — %s", exc)

        return json.dumps(items)

    return (_DEF, _impl)


register_tool("news_api.search", _factory)
