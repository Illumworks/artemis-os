"""Tool: grants_gov.search

Search Grants.gov for federal grant opportunities — no API key required.

Uses the public Search2 REST endpoint (POST JSON). The opportunity list lives
under ``data.oppHits``; each hit carries ``number`` (the opportunity number),
``id``, ``title``, ``agency``, ``closeDate`` and ``oppStatus``.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_SEARCH2_URL = "https://api.grants.gov/v1/api/search2"
# Open + forecasted opportunities are the actionable ones for outreach.
_OPP_STATUSES = "forecasted|posted"
_VIEW_BASE = "https://www.grants.gov/search-results-detail/"
_DEFAULT_ROWS = 25

# Lightweight relevance gate — keep grants whose title touches our domain.
_RELEVANCE_TERMS = (
    "literacy",
    "reading",
    "education",
    "tutor",
    "dyslexia",
    "student",
    "school",
    "esea",
    "title i",
)


def _is_relevant(title: str) -> bool:
    lowered = title.lower()
    return any(term in lowered for term in _RELEVANCE_TERMS)


def _parse_opp_hits(payload: dict[str, Any], rows: int) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    hits = data.get("oppHits") or []
    out: list[dict[str, Any]] = []
    for hit in hits:
        title = str(hit.get("title", "")).strip()
        if title and not _is_relevant(title):
            continue
        opp_id = str(hit.get("id", "")).strip()
        out.append(
            {
                "title": title,
                "opportunityNumber": str(hit.get("number", "")).strip(),
                "closeDate": str(hit.get("closeDate", "")).strip(),
                "agency": str(hit.get("agency", "")).strip(),
                "url": f"{_VIEW_BASE}{opp_id}" if opp_id else "",
            }
        )
        if len(out) >= rows:
            break
    return out


_DEF = Tool(
    name="grants_gov.search",
    description=(
        "Search Grants.gov (public Search2 API, no key) for federal grant "
        "opportunities matching a keyword. Returns up to `rows` items as JSON "
        "[{title, opportunityNumber, closeDate, agency, url}], filtered to "
        "literacy/education relevance. Returns [] on any error (graceful empty)."
    ),
    input_schema={
        "type": "object",
        "required": ["keyword"],
        "properties": {
            "keyword": {
                "type": "string",
                "description": "Search keyword, e.g. 'literacy'.",
            },
            "rows": {
                "type": "integer",
                "description": "Max opportunities to return (default 25).",
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        keyword: str = str(arguments.get("keyword", "")).strip()
        if not keyword:
            return json.dumps([])
        rows = int(arguments.get("rows", _DEFAULT_ROWS) or _DEFAULT_ROWS)

        body = {"keyword": keyword, "rows": rows, "oppStatuses": _OPP_STATUSES}
        try:
            async with ScoutHttpClient(timeout=20.0) as http:
                resp = await http.post(_SEARCH2_URL, json=body)
            if resp.status_code != 200:
                logger.warning("grants_gov.search: HTTP %d from Search2", resp.status_code)
                return json.dumps([])
            payload = resp.json()
        except Exception as exc:
            logger.warning("grants_gov.search(%r): error — %s", keyword, exc)
            return json.dumps([])

        if payload.get("errorcode") not in (0, None):
            logger.warning("grants_gov.search: API errorcode=%s", payload.get("errorcode"))
            return json.dumps([])

        return json.dumps(_parse_opp_hits(payload, rows))

    return (_DEF, _impl)


register_tool("grants_gov.search", _factory)
