"""Tool: procurement_portal.fetch

Search SAM.gov contract opportunities via the public Get Opportunities API v2.

Requires ``SAM_API_KEY`` (api.data.gov key) in the environment. When unset,
returns a stub string explaining how to configure the key. On any HTTP/API/
JSON error, returns ``[]`` rather than raising so procurement scouts degrade
gracefully.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.sam.gov/opportunities/v2/search"
_NEEDS_KEY = (
    "STUB: SAM.gov opportunities need an api.data.gov key. Set SAM_API_KEY in the .env file."
)
_DEFAULT_KEYWORD = "literacy"
_DEFAULT_LIMIT = 25
_DEFAULT_LOOKBACK_DAYS = 30


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_keyword(arguments: dict[str, Any]) -> str:
    query = _clean(arguments.get("query", ""))
    if query:
        return query

    keyword = _clean(arguments.get("keyword", ""))
    if keyword:
        return keyword

    keywords = arguments.get("keywords")
    if isinstance(keywords, list):
        joined = " ".join(_clean(item) for item in keywords if _clean(item))
        if joined:
            return joined

    return _DEFAULT_KEYWORD


def _coerce_limit(raw: Any) -> int:
    try:
        limit = int(raw or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return _DEFAULT_LIMIT
    return max(1, min(limit, _DEFAULT_LIMIT))


def _date_window(arguments: dict[str, Any]) -> tuple[str, str]:
    lookback_days_raw = arguments.get("lookbackDays", _DEFAULT_LOOKBACK_DAYS)
    try:
        lookback_days = int(lookback_days_raw or _DEFAULT_LOOKBACK_DAYS)
    except (TypeError, ValueError):
        lookback_days = _DEFAULT_LOOKBACK_DAYS
    lookback_days = max(1, lookback_days)

    posted_to = date.today()
    posted_from = posted_to - timedelta(days=lookback_days)
    return (
        posted_from.strftime("%m/%d/%Y"),
        posted_to.strftime("%m/%d/%Y"),
    )


def _parse_opportunities(payload: dict[str, Any]) -> list[dict[str, str]]:
    items = payload.get("opportunitiesData")
    if not isinstance(items, list):
        return []

    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": _clean(item.get("title", "")),
                "solicitation_number": _clean(item.get("solicitationNumber", "")),
                "agency": _clean(item.get("fullParentPathName", "")),
                "posted_date": _clean(item.get("postedDate", "")),
                "response_deadline": _clean(item.get("responseDeadLine", "")),
                "url": _clean(item.get("uiLink", "")),
                "description": _clean(item.get("description", "")),
                "naics": _clean(item.get("naicsCode", "")),
            }
        )
    return out


_DEF = Tool(
    name="procurement_portal.fetch",
    description=(
        "Search SAM.gov contract opportunities (real API, requires "
        "SAM_API_KEY). Returns JSON "
        "[{title, solicitation_number, agency, posted_date, "
        "response_deadline, url, description, naics}]. Returns a stub string "
        "if no key is configured, [] on any error."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "keyword": {"type": "string"},
            "keywords": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer"},
            "lookbackDays": {"type": "integer"},
            "ptype": {"type": "string"},
            "state": {"type": "string"},
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        api_key = os.getenv("SAM_API_KEY", "")
        if not api_key:
            return _NEEDS_KEY

        keyword = _normalize_keyword(arguments)
        limit = _coerce_limit(arguments.get("limit"))
        posted_from, posted_to = _date_window(arguments)

        params = {
            "api_key": api_key,
            "keyword": keyword,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "limit": limit,
        }
        ptype = _clean(arguments.get("ptype", ""))
        if ptype:
            params["ptype"] = ptype

        try:
            async with ScoutHttpClient(timeout=20.0, rate_limit=1.0) as http:
                resp = await http.get(_SEARCH_URL, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "procurement_portal.fetch(%r): HTTP %d",
                    keyword,
                    resp.status_code,
                )
                return json.dumps([])
            payload = resp.json()
        except Exception as exc:
            logger.warning("procurement_portal.fetch(%r): error — %s", keyword, exc)
            return json.dumps([])

        if not isinstance(payload, dict):
            logger.warning("procurement_portal.fetch(%r): non-object payload", keyword)
            return json.dumps([])
        if payload.get("errorCode") or payload.get("errorMessage"):
            logger.warning(
                "procurement_portal.fetch(%r): API error %s %s",
                keyword,
                payload.get("errorCode"),
                payload.get("errorMessage"),
            )
            return json.dumps([])

        return json.dumps(_parse_opportunities(payload))

    return (_DEF, _impl)


register_tool("procurement_portal.fetch", _factory)
