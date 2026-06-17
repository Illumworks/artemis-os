"""TX ESBD (Electronic State Business Daily) adapter for the Procurement Scout.

TX ESBD at ``www.txsmartbuy.gov/esbd`` is the Texas Comptroller's statewide
e-procurement portal.  State agencies, TEA, and many co-ops post solicitations
here.  Searching ESBD supplements Bonfire (which covers specific district-level
ISD feeds) with state-agency and co-op RFPs.

Access status (2026-06-16)
--------------------------
TxSmartBuy/ESBD is built on **NetSuite SuiteCommerce Advanced**, a JavaScript SPA.
All data is loaded via Backbone.js ``fetch()`` calls to SuiteScript ``*.ss``
endpoints (``ESBD.Service.ss``) that require a valid NetSuite session cookie.

- ``robots.txt`` disallows all crawlers.
- The ESBD main page renders only a shell ``<div id="main">`` with no data.
- No public RSS, Atom, or JSON API is exposed unauthenticated.
- No data is available on Texas Open Data (data.texas.gov).

The SuiteScript CSV export endpoint (``services/ESBD.Service.ss?isCSV=true``)
follows the same authentication requirement.

Design choice
-------------
The adapter is **structurally complete** — it constructs the correct query URL
and parses the expected JSON shape returned by ``ESBD.Service.ss`` — but until
a NetSuite session is available, every live call returns ``[]`` gracefully.

Future path
-----------
Option A: Obtain a free vendor account on TxSmartBuy and inject the resulting
NS session cookies (see §6 of the build plan — "IonWave gap decision" analogy).
Option B: Use Playwright to render the ESBD page and extract the rendered HTML.
Option C: Check whether TxSmartBuy publishes an Open API in a future version.

The return contract is identical to the Bonfire adapter::

    {
        "portal_id": "esbd_TX",
        "state": "TX",
        "rfp_id": str,           # internalid from NS
        "title": str,
        "agency": str,
        "posted_date": str,      # ISO YYYY-MM-DD or ""
        "due_date": str,         # ISO YYYY-MM-DD or ""
        "source_url": str,       # https://www.txsmartbuy.gov/esbd/<internalid>
        "description": str,
        "scope_text": str,       # "" — not available from listing
        "district_id": str,      # "" — TX state-wide
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any

from artemis.scouts._http import ScoutHttpClient

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

PORTAL_ID = "esbd_TX"
STATE = "TX"

ESBD_BASE_URL = "https://www.txsmartbuy.gov"
ESBD_LIST_URL = f"{ESBD_BASE_URL}/esbd"

# The SuiteScript CSV/JSON service endpoint (requires a valid NS session cookie).
# Full path: extensions/CPA/CPAMain/1.0.0/services/ESBD.Service.ss
_ESBD_SERVICE_PATH = "extensions/CPA/CPAMain/1.0.0/services/ESBD.Service.ss"
ESBD_SERVICE_URL = f"{ESBD_BASE_URL}/{_ESBD_SERVICE_PATH}"

# Browser UA — TxSmartBuy blocks non-browser UAs on the SPA shell.
_ESBD_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# NIGP class-item codes relevant to K-12 education / literacy
# 915: Educational Services; 918: Library/School Equipment; 920: Testing
_DEFAULT_NIGP_CODES = ("915", "918", "920", "924")

# Maximum records to request in a single call
_MAX_RECORDS = 100

# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

# MM/DD/YYYY — standard TxSmartBuy date format in service responses
_SLASH_DATE_RE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


def _parse_esbd_date(raw: str) -> str:
    """Normalise an ESBD date string to ISO YYYY-MM-DD.

    ESBD service responses return dates as ``MM/DD/YYYY``.
    Returns ``""`` on failure.
    """
    if not raw:
        return ""
    raw = raw.strip()
    m = _SLASH_DATE_RE.search(raw)
    if m:
        month, day, year = m.group(1), m.group(2), m.group(3)
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    return ""


# ---------------------------------------------------------------------------
# JSON response parser
#
# ESBD.Service.ss returns (when authenticated) a JSON object with:
#   {
#     "lines": [
#       {
#         "internalid": "12345",
#         "eventTitle": "K-12 Literacy Curriculum",
#         "agencyName": "Texas Education Agency",
#         "solicitationId": "DIR-ESS-2026-001",
#         "startDate": "06/01/2026",
#         "endDate": "07/15/2026",
#         "nigpCode": "915",
#         "description": "...",
#         ...
#       },
#       ...
#     ],
#     "totalRecordsFound": 42,
#     "recordsPerPage": 25,
#     "page": 1
#   }
# ---------------------------------------------------------------------------


def _parse_esbd_json(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parse an ESBD.Service.ss JSON response into posting dicts.

    Parameters
    ----------
    payload:
        Parsed JSON object from the ESBD service endpoint.

    Returns
    -------
    list[dict[str, Any]]
        One posting per ``lines`` element.  Returns ``[]`` on any parse error.
    """
    lines = payload.get("lines")
    if not isinstance(lines, list):
        _logger.debug("ESBD: JSON payload has no 'lines' list — empty or wrong shape")
        return []

    postings: list[dict[str, Any]] = []
    for item in lines:
        if not isinstance(item, dict):
            continue

        internal_id = str(item.get("internalid", "")).strip()
        sol_id = str(item.get("solicitationId", "")).strip()
        rfp_id = sol_id or internal_id
        if not rfp_id:
            continue

        title = str(item.get("eventTitle", "")).strip()
        agency = str(item.get("agencyName", "")).strip()
        description = str(item.get("description", title)).strip()
        posted_date = _parse_esbd_date(str(item.get("startDate", "")))
        due_date = _parse_esbd_date(str(item.get("endDate", "")))

        source_url = (
            f"{ESBD_LIST_URL}/{internal_id}" if internal_id else ESBD_LIST_URL
        )

        postings.append(
            {
                "portal_id": PORTAL_ID,
                "state": STATE,
                "rfp_id": rfp_id,
                "title": title,
                "agency": agency,
                "posted_date": posted_date,
                "due_date": due_date,
                "source_url": source_url,
                "description": description,
                "scope_text": "",
                "district_id": "",  # ESBD is TX state-wide; no per-district id
            }
        )

    return postings


# ---------------------------------------------------------------------------
# Public fetcher
# ---------------------------------------------------------------------------


async def fetch_esbd_opportunities(
    http: ScoutHttpClient,
    *,
    keyword: str = "literacy",
    ns_session_cookie: str | None = None,
    page: int = 1,
    records_per_page: int = _MAX_RECORDS,
) -> list[dict[str, Any]]:
    """Fetch ESBD solicitations from TxSmartBuy.

    Parameters
    ----------
    http:
        Shared ``ScoutHttpClient`` instance.
    keyword:
        Search keyword to filter solicitations.
    ns_session_cookie:
        Optional ``NetSuite`` session cookie value (``nlsid`` or similar).
        When ``None``, the request will hit the NS SuiteScript file without
        a valid session and return an HTML shell instead of JSON — ``[]`` is
        returned gracefully.
    page:
        Page number to request (1-based).
    records_per_page:
        Records to request per page.

    Returns
    -------
    list[dict[str, Any]]
        Parsed solicitation postings in the standard tool-shape format.
        Returns ``[]`` on any error or when no valid session is available.

    Notes
    -----
    Access wall: TxSmartBuy ESBD is a NetSuite SuiteCommerce Advanced SPA.
    Data is served by ``.ss`` SuiteScript files that require an authenticated NS
    session.  Without a session, all calls return the HTML page shell.
    Until a valid session is provided (via vendor registration or browser
    automation), this function always returns ``[]``.
    """
    headers: dict[str, str] = {
        "User-Agent": _ESBD_UA,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": ESBD_LIST_URL,
    }
    if ns_session_cookie:
        headers["Cookie"] = ns_session_cookie

    params: dict[str, Any] = {
        "page": page,
        "recordsPerPage": records_per_page,
        "keyword": keyword,
        "urlRoot": "esbd",
    }

    try:
        resp = await http.get(ESBD_SERVICE_URL, params=params, headers=headers)
    except Exception as exc:
        _logger.warning("ESBD: HTTP error fetching %s: %s", ESBD_SERVICE_URL, exc)
        return []

    if resp.status_code != 200:
        _logger.warning(
            "ESBD: unexpected HTTP %d from %s", resp.status_code, ESBD_SERVICE_URL
        )
        return []

    try:
        content_type = resp.headers.get("content-type", "")
        if "json" not in content_type.lower():
            # HTML shell returned — no session or JS-only rendering.
            _logger.info(
                "ESBD: service returned HTML (not JSON) — NS session required; returning []"
            )
            return []
        payload = resp.json()
    except Exception as exc:
        _logger.warning("ESBD: failed to parse response: %s", exc)
        return []

    if not isinstance(payload, dict):
        _logger.warning("ESBD: unexpected payload type %s", type(payload).__name__)
        return []

    return _parse_esbd_json(payload)
