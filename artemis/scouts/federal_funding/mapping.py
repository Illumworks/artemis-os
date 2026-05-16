"""Mapping functions: raw client models → finding dicts for the Federal Funding Scout.

Each mapper returns a finding dict ready for ``emit_signals()``.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from artemis.scouts.federal_funding.client import FedRegDocument, GrantOpportunity, RssItem

_logger = logging.getLogger(__name__)

_DISCOVERED_BY = "federal_funding_scout"
_DISTRICT_ID = "STATE_NATIONAL"

# ---------------------------------------------------------------------------
# Urgency / reason-code helpers
# ---------------------------------------------------------------------------

_LITERACY_KEYWORDS = frozenset(
    [
        "literacy",
        "reading",
        "dyslexia",
        "biliteracy",
        "assessment",
        "curriculum",
        "clsd",
        "esser",
        "title i",
        "idea",
    ]
)


def _has_literacy_keyword(text: str) -> bool:
    """Return True if *text* contains any tracked literacy keyword."""
    lower = text.lower()
    return any(kw in lower for kw in _LITERACY_KEYWORDS)


def _days_until(date_str: str | None) -> int | None:
    """Return calendar days from today until *date_str* (YYYY-MM-DD).

    Returns ``None`` when the string is absent or unparseable.
    """
    if not date_str:
        return None
    try:
        deadline = date.fromisoformat(date_str)
        return (deadline - date.today()).days
    except ValueError:
        return None


def _urgency_from_days(days: int | None, title: str) -> str:
    """Derive urgency tier from days-to-deadline and title keywords."""
    if days is None:
        return "enrichment"
    if days <= 30 and _has_literacy_keyword(title):
        return "hot"
    if 30 < days <= 90:
        return "standard"
    return "enrichment"


def _reason_codes(text: str, close_date: str | None) -> list[str]:
    """Build reason code list from document/grant text and deadline."""
    lower = text.lower()
    codes: list[str] = []

    if "comprehensive literacy" in lower or "clsd" in lower:
        codes.append("CLSD_ANNOUNCEMENT")
    if "esser" in lower:
        codes.append("ESSER_CLIFF_REFERENCE")

    days = _days_until(close_date)
    if days is not None and days <= 90 and _has_literacy_keyword(text):
        codes.append("FEDERAL_GRANT_DEADLINE")
    elif not codes:
        codes.append("FEDERAL_GRANT_OPEN")

    return codes


# ---------------------------------------------------------------------------
# Mapper functions
# ---------------------------------------------------------------------------


def fed_reg_to_finding(doc: FedRegDocument) -> dict[str, Any]:
    """Convert a FedRegDocument to a finding dict."""
    combined_text = f"{doc.title} {doc.abstract}"
    snippet = f"{doc.title}. {doc.abstract}"[:500].strip()

    return {
        "sourceType": "federal_register",
        "discoveredBy": _DISCOVERED_BY,
        "districtId": _DISTRICT_ID,
        "reasonCodes": _reason_codes(combined_text, None),
        "urgency": "enrichment",  # Federal Register docs rarely have deadlines
        "evidence": snippet,
        "metadata": {
            "document_number": doc.document_number,
            "publication_date": doc.publication_date,
            "html_url": doc.html_url,
        },
    }


def grant_to_finding(grant: GrantOpportunity) -> dict[str, Any]:
    """Convert a GrantOpportunity to a finding dict."""
    combined_text = " ".join(filter(None, [grant.title, grant.agency_name, grant.synopsis or ""]))
    snippet = f"{grant.title}. Agency: {grant.agency_name}."
    if grant.synopsis:
        snippet = f"{snippet} {grant.synopsis}"
    snippet = snippet[:500].strip()

    days = _days_until(grant.close_date)
    urgency = _urgency_from_days(days, grant.title)

    return {
        "sourceType": "grants_gov",
        "discoveredBy": _DISCOVERED_BY,
        "districtId": _DISTRICT_ID,
        "reasonCodes": _reason_codes(combined_text, grant.close_date),
        "urgency": urgency,
        "evidence": snippet,
        "metadata": {
            "opportunity_id": grant.opportunity_id,
            "agency_name": grant.agency_name,
            "close_date": grant.close_date,
            "award_floor": grant.award_floor,
        },
    }


def rss_item_to_finding(item: RssItem) -> dict[str, Any]:
    """Convert an RssItem to a finding dict."""
    combined_text = f"{item.title} {item.description}"
    snippet = f"{item.title}. {item.description}"[:500].strip()

    return {
        "sourceType": "district_press",
        "discoveredBy": _DISCOVERED_BY,
        "districtId": _DISTRICT_ID,
        "reasonCodes": _reason_codes(combined_text, None),
        "urgency": "enrichment",
        "evidence": snippet,
        "metadata": {
            "link": item.link,
            "pub_date": item.pub_date,
        },
    }
