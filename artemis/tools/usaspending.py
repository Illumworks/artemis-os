"""Tool: usaspending.search

Search USASpending.gov for recent federal education grant awards to our
priority states (TX, FL, IL, IN, MD, MO) — free public API, no key required.

Uses the public POST /api/v2/search/spending_by_award/ endpoint.  Filters by:
- award_type_codes 02–05 (grants and cooperative agreements)
- recipient_locations: one entry per priority state (or the caller's state list)
- program_numbers (CFDA / Assistance Listing): education programs listed below
- time_period: last ``lookback_days`` days (default 365; state-level grants
  are awarded once per fiscal year, so a generous window is correct)

Returns a list of award dicts:
  [{award_id, recipient_name, recipient_state, cfda_number, cfda_program_title,
    amount, start_date, end_date, last_modified_date, description, url}]

The ``url`` field points to the USASpending.gov detail page for the award.
Returns [] on any error (graceful empty).

Registered at import time via ``register_tool``. Imported by
``artemis/tools/__init__.py`` so factories fire on first ``import artemis.tools``.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from artemis.agent.types import Tool, ToolImpl
from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.registry import register_tool

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
_DETAIL_BASE = "https://www.usaspending.gov/award/"

# Grant / cooperative-agreement award type codes in USASpending taxonomy.
_GRANT_AWARD_TYPES = ["02", "03", "04", "05"]

# CFDA (Assistance Listing) numbers for the education programs we track.
# - 84.010: Title I Grants to Local Educational Agencies
# - 84.027: Special Education Grants to States (IDEA Part B)
# - 84.048: Career and Technical Education (Perkins)
# - 84.173: Special Education Preschool Grants (IDEA)
# - 84.213: Even Start Family Literacy Program
# - 84.357: Reading First State Grants
# - 84.358: Rural Education Achievement Program
# - 84.371: Comprehensive Literacy State Development (CLSD) — direct literacy hit
# - 84.411: ESEA — Title II Part A Supporting Effective Instruction
# - 84.424: Student Support and Academic Enrichment Program (SSAE)
_EDUCATION_CFDA = [
    "84.010",
    "84.027",
    "84.048",
    "84.173",
    "84.213",
    "84.357",
    "84.358",
    "84.371",
    "84.411",
    "84.424",
]

_DEFAULT_PRIORITY_STATES = ["TX", "FL", "IL", "IN", "MD", "MO"]
_DEFAULT_LOOKBACK_DAYS = 365
_DEFAULT_LIMIT = 25

# Fields we request from the API (must match the Non-Loan Assistance Award mappings).
_FIELDS = [
    "Award ID",
    "Recipient Name",
    "recipient_location_state_code",
    "Awarding Agency",
    "Award Amount",
    "cfda_number",
    "cfda_program_title",
    "Start Date",
    "End Date",
    "Last Modified Date",
    "Description",
]


def _build_time_period(lookback_days: int) -> list[dict[str, str]]:
    end = date.today()
    start = end - timedelta(days=lookback_days)
    return [{"start_date": start.isoformat(), "end_date": end.isoformat()}]


def _build_recipient_locations(states: list[str]) -> list[dict[str, str]]:
    return [{"country": "USA", "state": s.upper()} for s in states if s]


def _parse_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Map a single API result dict to our canonical award shape."""
    generated_id: str = str(raw.get("generated_internal_id") or "").strip()
    url = f"{_DETAIL_BASE}{generated_id}" if generated_id else ""

    return {
        "award_id": str(raw.get("Award ID") or "").strip(),
        "recipient_name": str(raw.get("Recipient Name") or "").strip(),
        "recipient_state": str(raw.get("recipient_location_state_code") or "").strip(),
        "cfda_number": str(raw.get("cfda_number") or "").strip(),
        "cfda_program_title": str(raw.get("cfda_program_title") or "").strip(),
        "amount": raw.get("Award Amount"),
        "start_date": str(raw.get("Start Date") or "").strip() or None,
        "end_date": str(raw.get("End Date") or "").strip() or None,
        "last_modified_date": str(raw.get("Last Modified Date") or "").strip() or None,
        "description": str(raw.get("Description") or "").strip(),
        "url": url,
    }


_DEF = Tool(
    name="usaspending.search",
    description=(
        "Search USASpending.gov (free public API, no key) for recent federal "
        "education grant awards to our priority states (TX, FL, IL, IN, MD, MO). "
        "Covers Title I (84.010), IDEA (84.027), Comprehensive Literacy/CLSD (84.371), "
        "SSAE (84.424), and related programs. Returns up to `limit` awards as JSON "
        "[{award_id, recipient_name, recipient_state, cfda_number, cfda_program_title, "
        "amount, start_date, end_date, last_modified_date, description, url}]. "
        "Returns [] on any error (graceful empty). "
        "Use this to flag: district/state received a federal education grant → "
        "watch for a related RFP (grant→procurement chaining)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "states": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "2-letter state codes to include (default: all 6 priority states "
                    "TX, FL, IL, IN, MD, MO)."
                ),
            },
            "cfda_numbers": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "CFDA / Assistance Listing numbers to filter by "
                    "(default: full education list including 84.010, 84.027, 84.371, 84.424, etc.)."
                ),
            },
            "lookback_days": {
                "type": "integer",
                "description": (
                    "How many days back to search (default 365 — "
                    "state-level grants are awarded once per fiscal year)."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Max awards to return (default 25).",
            },
        },
    },
)


def _factory(ctx: ToolContext) -> tuple[Tool, ToolImpl]:
    async def _impl(arguments: dict[str, Any]) -> str:
        raw_states = arguments.get("states")
        states: list[str] = (
            [str(s).strip().upper() for s in raw_states if str(s).strip()]
            if isinstance(raw_states, list) and raw_states
            else list(_DEFAULT_PRIORITY_STATES)
        )

        raw_cfda = arguments.get("cfda_numbers")
        cfda_numbers: list[str] = (
            [str(c).strip() for c in raw_cfda if str(c).strip()]
            if isinstance(raw_cfda, list) and raw_cfda
            else list(_EDUCATION_CFDA)
        )

        try:
            lookback_days = int(arguments.get("lookback_days") or _DEFAULT_LOOKBACK_DAYS)
            lookback_days = max(1, lookback_days)
        except (TypeError, ValueError):
            lookback_days = _DEFAULT_LOOKBACK_DAYS

        try:
            limit = int(arguments.get("limit") or _DEFAULT_LIMIT)
            limit = max(1, min(limit, 100))
        except (TypeError, ValueError):
            limit = _DEFAULT_LIMIT

        body: dict[str, Any] = {
            "filters": {
                "award_type_codes": _GRANT_AWARD_TYPES,
                "recipient_locations": _build_recipient_locations(states),
                "time_period": _build_time_period(lookback_days),
                "program_numbers": cfda_numbers,
            },
            "fields": _FIELDS,
            "sort": "Last Modified Date",
            "order": "desc",
            "limit": limit,
            "page": 1,
        }

        try:
            async with ScoutHttpClient(timeout=30.0, rate_limit=2.0) as http:
                resp = await http.post(_SEARCH_URL, json=body)
        except Exception as exc:
            logger.warning("usaspending.search: HTTP error — %s", exc)
            return json.dumps([])

        if resp.status_code != 200:
            logger.warning("usaspending.search: HTTP %d from API", resp.status_code)
            return json.dumps([])

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning("usaspending.search: JSON parse error — %s", exc)
            return json.dumps([])

        if not isinstance(payload, dict):
            logger.warning("usaspending.search: non-dict payload")
            return json.dumps([])

        messages = payload.get("messages") or []
        for msg in messages:
            logger.debug("usaspending.search: API message: %s", msg)

        results = payload.get("results") or []
        if not isinstance(results, list):
            logger.warning("usaspending.search: 'results' is not a list")
            return json.dumps([])

        awards: list[dict[str, Any]] = []
        for raw in results:
            if not isinstance(raw, dict):
                continue
            try:
                awards.append(_parse_result(raw))
            except Exception as exc:
                logger.warning("usaspending.search: failed to parse result %r — %s", raw, exc)

        logger.info(
            "usaspending.search: returned %d awards (states=%s, lookback=%dd)",
            len(awards),
            ",".join(states),
            lookback_days,
        )
        return json.dumps(awards)

    return (_DEF, _impl)


register_tool("usaspending.search", _factory)
