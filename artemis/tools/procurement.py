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
_DEFAULT_NAICS_CODES = ("611110", "611710", "611310", "611691", "624310")
_EDUCATION_TERMS = (
    "education",
    "school",
    "schools",
    "district",
    "student",
    "students",
    "classroom",
    "teacher",
    "teachers",
    "k-12",
    "elementary",
    "secondary",
)
_SOLUTION_TERMS = (
    "literacy",
    "reading",
    "dyslexia",
    "phonics",
    "curriculum",
    "assessment",
    "tutoring",
    "intervention",
    "english language arts",
    "ela",
    "screener",
)
_NEGATIVE_TERMS = (
    "aircraft",
    "ammunition",
    "bearing",
    "cable assembly",
    "combat",
    "compressor",
    "defense logistics",
    "forklift",
    "marine corps",
    "missile",
    "munition",
    "nsn",
    "radar",
    "replacement part",
    "spare part",
    "valve",
    "weapon",
)


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


def _normalize_naics(arguments: dict[str, Any]) -> str:
    raw_codes = arguments.get("naics") or arguments.get("ncode")
    if isinstance(raw_codes, str):
        pieces = [part.strip() for part in raw_codes.split(",")]
    elif isinstance(raw_codes, list):
        pieces = [_clean(part) for part in raw_codes]
    else:
        pieces = list(_DEFAULT_NAICS_CODES)

    seen: set[str] = set()
    normalized: list[str] = []
    for code in pieces:
        if code and code not in seen:
            seen.add(code)
            normalized.append(code)
    return ",".join(normalized or _DEFAULT_NAICS_CODES)


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


def _is_education_relevant(item: dict[str, str]) -> bool:
    title = item.get("title", "").lower()
    description = item.get("description", "").lower()
    agency = item.get("agency", "").lower()
    naics = item.get("naics", "")
    combined = " ".join(part for part in (title, description, agency) if part)

    has_solution_term = any(term in combined for term in _SOLUTION_TERMS)
    has_education_context = any(term in combined for term in _EDUCATION_TERMS)
    has_negative_term = any(term in combined for term in _NEGATIVE_TERMS)
    has_education_naics = naics in _DEFAULT_NAICS_CODES

    if not has_solution_term:
        return False
    if not (has_education_context or has_education_naics):
        return False
    return not has_negative_term or has_education_naics


def _dedupe_opportunities(items: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, str]] = []
    for item in items:
        key = (
            item.get("solicitation_number", "") or item.get("url", ""),
            item.get("title", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


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
            "title": {"type": "string"},
            "limit": {"type": "integer"},
            "lookbackDays": {"type": "integer"},
            "ptype": {"type": "string"},
            "state": {"type": "string"},
            "naics": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
            "ncode": {
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}},
                ]
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        api_key = os.getenv("SAM_API_KEY", "")
        if not api_key:
            return _NEEDS_KEY

        keyword = _normalize_keyword(arguments)
        title = _clean(arguments.get("title", ""))
        limit = _coerce_limit(arguments.get("limit"))
        posted_from, posted_to = _date_window(arguments)
        ncode = _normalize_naics(arguments)

        params = {
            "api_key": api_key,
            "postedFrom": posted_from,
            "postedTo": posted_to,
            "limit": limit,
            "ncode": ncode,
        }
        if title:
            params["title"] = title
        else:
            params["keyword"] = keyword
        ptype = _clean(arguments.get("ptype", ""))
        if ptype:
            params["ptype"] = ptype

        try:
            async with ScoutHttpClient(timeout=20.0, rate_limit=1.0) as http:
                resp = await http.get(_SEARCH_URL, params=params)
            if resp.status_code != 200:
                logger.warning(
                    "procurement_portal.fetch(%r): HTTP %d",
                    title or keyword,
                    resp.status_code,
                )
                return json.dumps([])
            payload = resp.json()
        except Exception as exc:
            logger.warning("procurement_portal.fetch(%r): error — %s", title or keyword, exc)
            return json.dumps([])

        if not isinstance(payload, dict):
            logger.warning("procurement_portal.fetch(%r): non-object payload", title or keyword)
            return json.dumps([])
        if payload.get("errorCode") or payload.get("errorMessage"):
            logger.warning(
                "procurement_portal.fetch(%r): API error %s %s",
                title or keyword,
                payload.get("errorCode"),
                payload.get("errorMessage"),
            )
            return json.dumps([])

        projected = _parse_opportunities(payload)
        relevant = [item for item in projected if _is_education_relevant(item)]
        return json.dumps(_dedupe_opportunities(relevant)[:limit])

    return (_DEF, _impl)


register_tool("procurement_portal.fetch", _factory)
