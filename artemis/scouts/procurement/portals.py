"""Statewide procurement portal adapters for the Procurement Scout.

Each portal entry in ``PORTAL_REGISTRY`` describes a statewide e-procurement
portal. ``fetch_portal_postings`` fetches and parses HTML from the portal,
filters postings for literacy relevance, and returns structured dicts.

URLs are approximate and marked TODO: verify URL — real scraping happens at
runtime; tests mock the HTTP client.
"""

from __future__ import annotations

import html.parser
import logging
from typing import Any

from artemis.scouts._http import ScoutHttpClient
from artemis.scouts.procurement.bonfire import (
    BONFIRE_REGISTRY,
    fetch_bonfire_opportunities,
)
from artemis.scouts.procurement.emma import (
    PORTAL_ID as EMMA_PORTAL_ID,
)
from artemis.scouts.procurement.emma import (
    fetch_emma_opportunities,
)
from artemis.scouts.procurement.esbd import (
    PORTAL_ID as ESBD_PORTAL_ID,
)
from artemis.scouts.procurement.esbd import (
    fetch_esbd_opportunities,
)

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Literacy relevance keywords — filter applied before returning postings
# ---------------------------------------------------------------------------

LITERACY_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "biliteracy",
    "assessment",
    "curriculum",
    "tutoring",
    "learning",
    "intervention",
    "outcomes-based",
    "obc",
]

# ---------------------------------------------------------------------------
# Portal registry
# ---------------------------------------------------------------------------

def _bonfire_portal_entries() -> dict[str, dict[str, Any]]:
    """Build PORTAL_REGISTRY entries for each Bonfire district in BONFIRE_REGISTRY.

    Key pattern: ``bonfire_<slug>`` (e.g. ``bonfire_dallasisd``).
    Each entry carries ``type: "rss"`` so ``fetch_portal_postings`` dispatches
    to the Bonfire adapter rather than the generic HTML scraper.
    """
    entries: dict[str, dict[str, Any]] = {}
    for entry in BONFIRE_REGISTRY:
        slug = entry["slug"]
        entries[f"bonfire_{slug}"] = {
            "state": entry["state"],
            "name": entry["name"],
            "url": f"https://{slug}.bonfirehub.com/opportunities/rss",
            "type": "rss",
            # Carry the full entry so fetch_portal_postings can pass it to the
            # Bonfire adapter without an extra lookup.
            "_bonfire_entry": entry,
        }
    return entries


PORTAL_REGISTRY: dict[str, dict[str, Any]] = {
    **_bonfire_portal_entries(),
    EMMA_PORTAL_ID: {
        "state": "MD",
        "name": "MD eMaryland Marketplace Advantage (eMMA)",
        "url": "https://emma.maryland.gov/page.aspx/en/rfp/request_browse_public",
        # Access wall: reCAPTCHA Enterprise required — adapter returns [] until resolved.
        "type": "emma",
    },
    ESBD_PORTAL_ID: {
        "state": "TX",
        "name": "TX Electronic State Business Daily (ESBD)",
        "url": "https://www.txsmartbuy.gov/esbd",
        # Access wall: NetSuite SuiteCommerce session required — adapter returns [] until resolved.
        "type": "esbd",
    },
    "CA_eprocurement": {
        "state": "CA",
        "name": "CA eProcurement",
        "url": "https://caleprocure.ca.gov/pages/Events/event-search.aspx",  # TODO: verify URL
        "type": "html_scrape",
    },
    "GA_procurement": {
        "state": "GA",
        "name": "GA Procurement Registry",
        "url": "https://ssl.doas.state.ga.us/PRSapp/PR_SEARCH_FORM.jsp",  # TODO: verify URL
        "type": "html_scrape",
    },
    "TX_smartbuy": {
        "state": "TX",
        "name": "TX SmartBuy",
        "url": "https://www.txsmartbuy.gov/esbd",  # TODO: verify URL
        "type": "html_scrape",
    },
    "FL_vbs": {
        "state": "FL",
        "name": "FL Vendor Bid System",
        "url": "https://www.myflorida.com/apps/vbs/vbs_www.search_form",  # TODO: verify URL
        "type": "html_scrape",
    },
    "IL_bidbuy": {
        "state": "IL",
        "name": "IL BidBuy",
        "url": "https://www.bidbuy.illinois.gov/bso/external/publicBids.sdo",  # TODO: verify URL
        "type": "html_scrape",
    },
    "IN_doa": {
        "state": "IN",
        "name": "IN Dept of Administration",
        "url": "https://www.in.gov/idoa/procurement/",  # TODO: verify URL
        "type": "html_scrape",
    },
    "MD_emma": {
        "state": "MD",
        "name": "MD eMaryland Marketplace",
        "url": "https://emaryland.buyspeed.com/bso/external/publicBids.sdo",  # TODO: verify URL
        "type": "html_scrape",
    },
    "MI_bid4michigan": {
        "state": "MI",
        "name": "MI Bid4Michigan",
        "url": "https://www.michigan.gov/dtmb/procurement/bid4michigan",  # TODO: verify URL
        "type": "html_scrape",
    },
    "MO_oa_procurement": {
        "state": "MO",
        "name": "MO Office of Administration Procurement",
        "url": "https://oa.mo.gov/purchasing/bid-opportunities",  # TODO: verify URL
        "type": "html_scrape",
    },
}

# ---------------------------------------------------------------------------
# HTML parser — extracts table rows and list items as candidate postings
# ---------------------------------------------------------------------------


class _PostingRowParser(html.parser.HTMLParser):
    """Minimal HTML parser that collects text from table rows and list items.

    Each ``<tr>`` or ``<li>`` is collected as a single candidate string.
    This is intentionally simple — procurement portals vary wildly in markup
    and real adapters would be portal-specific. For V1 we harvest any
    text-bearing row and let the keyword filter decide relevance.
    """

    def __init__(self) -> None:
        super().__init__()
        self._rows: list[str] = []
        self._current: list[str] = []
        self._in_row: bool = False
        self._depth: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("tr", "li"):
            self._in_row = True
            self._depth = 0
            self._current = []
        elif self._in_row:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("tr", "li") and self._in_row:
            row_text = " ".join(self._current).strip()
            if row_text:
                self._rows.append(row_text)
            self._in_row = False
            self._current = []
            self._depth = 0
        elif self._in_row and self._depth > 0:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._in_row:
            stripped = data.strip()
            if stripped:
                self._current.append(stripped)

    @property
    def rows(self) -> list[str]:
        return list(self._rows)


# ---------------------------------------------------------------------------
# Literacy filter
# ---------------------------------------------------------------------------


def _is_literacy_relevant(title: str, description: str) -> bool:
    """Return True if title or description contains at least one literacy keyword."""
    combined = (title + " " + description).lower()
    return any(kw in combined for kw in LITERACY_KEYWORDS)


# ---------------------------------------------------------------------------
# Generic posting parser — converts raw HTML rows to posting dicts
# ---------------------------------------------------------------------------


def _parse_postings_from_html(
    portal_id: str,
    state: str,
    source_url: str,
    html_text: str,
) -> list[dict[str, Any]]:
    """Parse HTML into raw posting dicts (before literacy filtering).

    Each row becomes one candidate posting.  Fields are extracted on a
    best-effort basis from the row text; blank values are acceptable.
    """
    parser = _PostingRowParser()
    try:
        parser.feed(html_text)
    except Exception as exc:
        _logger.warning("Portal %s: HTML parse error: %s", portal_id, exc)
        return []

    postings: list[dict[str, Any]] = []
    for i, row in enumerate(parser.rows):
        # Split row text into tokens; first token often contains an ID/number.
        tokens = row.split()
        if not tokens:
            continue

        # Heuristic extraction — real portals would need portal-specific logic.
        # For V1 we extract a minimal set and let the filter decide.
        rfp_id = tokens[0] if tokens else f"{portal_id}-{i}"
        title = row[:200]
        description = row

        postings.append(
            {
                "portal_id": portal_id,
                "state": state,
                "rfp_id": rfp_id,
                "title": title,
                "agency": "",
                "posted_date": "",
                "due_date": "",
                "source_url": source_url,
                "description": description,
                "scope_text": "",
            }
        )

    return postings


# ---------------------------------------------------------------------------
# Public fetcher
# ---------------------------------------------------------------------------


async def fetch_portal_postings(
    portal_id: str,
    portal: dict[str, Any],
    http: ScoutHttpClient,
    pdf_open_fn: Any = None,
) -> list[dict[str, Any]]:
    """Fetch and parse procurement postings from a statewide portal.

    Parameters
    ----------
    portal_id:
        Registry key (e.g. ``"CA_eprocurement"``).
    portal:
        Registry entry dict (state, name, url, type).
    http:
        Rate-limited HTTP client for all outbound requests.
    pdf_open_fn:
        Optional injectable PDF-open function (tests only).

    Returns
    -------
    list[dict[str, Any]]
        Literacy-relevant postings only.  Each dict has keys:
        portal_id, state, rfp_id, title, agency, posted_date, due_date,
        source_url, description, scope_text.
    """
    portal_type: str = portal.get("type", "html_scrape")

    # ------------------------------------------------------------------
    # RSS adapters (e.g. Bonfire) — dispatch to the dedicated fetcher.
    # These adapters return postings that already carry all required fields
    # including "district_id" for a precise districtId in findings.
    # ------------------------------------------------------------------
    if portal_type == "rss":
        bonfire_entry = portal.get("_bonfire_entry")
        if bonfire_entry is None:
            _logger.warning(
                "Portal %s: type=rss but no _bonfire_entry — skipping.",
                portal_id,
            )
            return []
        try:
            postings = await fetch_bonfire_opportunities(bonfire_entry, http)
        except Exception as exc:
            _logger.warning(
                "Portal %s: Bonfire fetch error — skipping: %s",
                portal_id,
                exc,
            )
            return []
        return [p for p in postings if _is_literacy_relevant(p["title"], p["description"])]

    # ------------------------------------------------------------------
    # eMMA (Maryland) adapter
    # ------------------------------------------------------------------
    if portal_type == "emma":
        try:
            postings = await fetch_emma_opportunities(http)
        except Exception as exc:
            _logger.warning(
                "Portal %s: eMMA fetch error — skipping: %s",
                portal_id,
                exc,
            )
            return []
        return [p for p in postings if _is_literacy_relevant(p["title"], p["description"])]

    # ------------------------------------------------------------------
    # TX ESBD adapter
    # ------------------------------------------------------------------
    if portal_type == "esbd":
        try:
            postings = await fetch_esbd_opportunities(http, keyword="literacy")
        except Exception as exc:
            _logger.warning(
                "Portal %s: ESBD fetch error — skipping: %s",
                portal_id,
                exc,
            )
            return []
        return [p for p in postings if _is_literacy_relevant(p["title"], p["description"])]

    # ------------------------------------------------------------------
    # HTML scrape path (original behaviour — unchanged)
    # ------------------------------------------------------------------
    url: str = portal.get("url", "")
    state: str = portal.get("state", "")

    try:
        resp = await http.get(url)
    except Exception as exc:
        _logger.warning("Portal %s: HTTP error fetching %s: %s", portal_id, url, exc)
        return []

    try:
        html_text: str = resp.text
    except Exception as exc:
        _logger.warning("Portal %s: failed to read response text: %s", portal_id, exc)
        return []

    raw_postings = _parse_postings_from_html(portal_id, state, url, html_text)

    # Apply literacy relevance filter.
    relevant: list[dict[str, Any]] = [
        p for p in raw_postings if _is_literacy_relevant(p["title"], p["description"])
    ]

    return relevant
