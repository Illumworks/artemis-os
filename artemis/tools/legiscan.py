"""Tools: legiscan.search, legiscan.get_bill

Real LegiScan client, gated on a free API key (stub-until-key).

LegiScan requires a free key (register at legiscan.com/legiscan). When
``LEGISCAN_API_KEY`` is unset the tools return a stub string explaining how to
obtain one. When set, they call the real GAITS REST API:

- ``op=getSearch`` returns lightweight refs under ``searchresult`` —
  ``{relevance, bill_id, change_hash}`` plus a numeric ``summary``. Bill text
  must be fetched separately via ``op=getBill``.
- ``op=getBill`` returns the full bill record under the ``bill`` key.

Free tier — rate-limited to 1 request/second via ``ScoutHttpClient``.

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.legiscan.com/"
_NEEDS_KEY = (
    "STUB: LegiScan needs a free API key — register at legiscan.com/legiscan "
    "and set LEGISCAN_API_KEY in the .env file."
)
_DEFAULT_STATE = "ALL"

# Surface bills whose getBill text touches our domain.
_RELEVANCE_TERMS = (
    "literacy",
    "reading",
    "screening",
    "screener",
    "dyslexia",
    "tutor",
    "education",
)


def _is_relevant(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in _RELEVANCE_TERMS)


def _parse_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    searchresult = payload.get("searchresult") or {}
    out: list[dict[str, Any]] = []
    for key, item in searchresult.items():
        if key == "summary" or not isinstance(item, dict):
            continue
        out.append(
            {
                "bill_id": item.get("bill_id"),
                "relevance": item.get("relevance"),
                "change_hash": item.get("change_hash"),
            }
        )
    return out


def _parse_bill(payload: dict[str, Any]) -> dict[str, Any]:
    bill = payload.get("bill") or {}
    last_action = ""
    history = bill.get("history")
    if isinstance(history, list) and history:
        last_action = str(history[-1].get("action", "")).strip()
    return {
        "bill_id": bill.get("bill_id"),
        "bill_number": str(bill.get("bill_number", "")).strip(),
        "title": str(bill.get("title", "")).strip(),
        "description": str(bill.get("description", "")).strip(),
        "state": str(bill.get("state", "")).strip(),
        "status_date": str(bill.get("status_date", "")).strip(),
        "url": str(bill.get("url", "")).strip(),
        "last_action": last_action,
    }


_DEF_SEARCH = Tool(
    name="legiscan.search",
    description=(
        "Search LegiScan for state legislation (real API, requires a free "
        "LEGISCAN_API_KEY). Returns JSON [{bill_id, relevance, change_hash}]; "
        "use legiscan.get_bill for full text. Returns a stub string if no key "
        "is configured, [] on any error."
    ),
    input_schema={
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "Search query string."},
            "state": {
                "type": "string",
                "description": "2-letter state code or 'ALL' (default ALL).",
            },
        },
    },
)

_DEF_GET_BILL = Tool(
    name="legiscan.get_bill",
    description=(
        "Retrieve a specific bill from LegiScan by bill ID (real API, requires "
        "a free LEGISCAN_API_KEY). Returns JSON {bill_id, bill_number, title, "
        "description, state, status_date, url, last_action}. Returns a stub "
        "string if no key is configured, {} on any error."
    ),
    input_schema={
        "type": "object",
        "required": ["billId"],
        "properties": {
            "billId": {"type": "string", "description": "LegiScan numeric bill_id."},
        },
    },
)


def _factory_search(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        api_key = os.getenv("LEGISCAN_API_KEY", "")
        if not api_key:
            return _NEEDS_KEY

        query: str = str(arguments.get("query", "")).strip()
        if not query:
            return json.dumps([])
        state: str = str(arguments.get("state", "") or _DEFAULT_STATE).strip()

        params = {"key": api_key, "op": "getSearch", "state": state, "query": query}
        try:
            async with ScoutHttpClient(timeout=20.0, rate_limit=1.0) as http:
                resp = await http.get(_BASE_URL, params=params)
            if resp.status_code != 200:
                logger.warning("legiscan.search: HTTP %d", resp.status_code)
                return json.dumps([])
            payload = resp.json()
        except Exception as exc:
            logger.warning("legiscan.search(%r): error — %s", query, exc)
            return json.dumps([])

        if payload.get("status") != "OK":
            logger.warning("legiscan.search: status=%s", payload.get("status"))
            return json.dumps([])

        return json.dumps(_parse_search(payload))

    return (_DEF_SEARCH, _impl)


def _factory_get_bill(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        api_key = os.getenv("LEGISCAN_API_KEY", "")
        if not api_key:
            return _NEEDS_KEY

        bill_id: str = str(arguments.get("billId", "")).strip()
        if not bill_id:
            return json.dumps({})

        params = {"key": api_key, "op": "getBill", "id": bill_id}
        try:
            async with ScoutHttpClient(timeout=20.0, rate_limit=1.0) as http:
                resp = await http.get(_BASE_URL, params=params)
            if resp.status_code != 200:
                logger.warning("legiscan.get_bill: HTTP %d", resp.status_code)
                return json.dumps({})
            payload = resp.json()
        except Exception as exc:
            logger.warning("legiscan.get_bill(%r): error — %s", bill_id, exc)
            return json.dumps({})

        if payload.get("status") != "OK":
            logger.warning("legiscan.get_bill: status=%s", payload.get("status"))
            return json.dumps({})

        bill = _parse_bill(payload)
        if bill.get("title") and not _is_relevant(f"{bill['title']} {bill['description']}"):
            logger.info("legiscan.get_bill: bill %s not domain-relevant", bill_id)
        return json.dumps(bill)

    return (_DEF_GET_BILL, _impl)


register_tool("legiscan.search", _factory_search)
register_tool("legiscan.get_bill", _factory_get_bill)
