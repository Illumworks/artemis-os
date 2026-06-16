"""Deterministic mapping: procurement portal postings → finding dicts.

This module is purely deterministic — no I/O, no async.
"""

from __future__ import annotations

import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Reason code constants
# ---------------------------------------------------------------------------

_RC_RFP_LITERACY = "RFP_LITERACY_POSTED"
_RC_RFP_ASSESSMENT = "RFP_ASSESSMENT_POSTED"
_RC_RFP_TUTORING = "RFP_TUTORING_POSTED"
_RC_RFP_DEADLINE_CRITICAL = "RFP_DEADLINE_CRITICAL"
_RC_RFP_EFFICACY = "RFP_EFFICACY_LANGUAGE"
_RC_RFP_OUTCOMES_BASED = "RFP_OUTCOMES_BASED_LANGUAGE"

# Days-to-close threshold for DEADLINE_CRITICAL
_DEADLINE_CRITICAL_DAYS = 14


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def days_to_close(due_date_str: str) -> int | None:
    """Parse *due_date_str* (ISO format YYYY-MM-DD) and return days until due.

    Returns ``None`` if the string is empty or cannot be parsed.
    """
    if not due_date_str:
        return None
    try:
        due = datetime.date.fromisoformat(due_date_str)
        return (due - datetime.date.today()).days
    except ValueError:
        return None


def _reason_codes(posting: dict[str, Any]) -> list[str]:
    """Build the list of reason codes for a posting."""
    title_lower = posting.get("title", "").lower()
    desc_lower = posting.get("description", "").lower()
    scope_lower = posting.get("scope_text", "").lower()
    combined = title_lower + " " + desc_lower

    codes: list[str] = [_RC_RFP_LITERACY]  # always present

    if "assessment" in combined:
        codes.append(_RC_RFP_ASSESSMENT)

    if "tutoring" in combined:
        codes.append(_RC_RFP_TUTORING)

    dtc = days_to_close(posting.get("due_date", ""))
    if dtc is not None and dtc <= _DEADLINE_CRITICAL_DAYS:
        codes.append(_RC_RFP_DEADLINE_CRITICAL)

    if (
        "efficacy" in scope_lower
        or "measurable growth" in scope_lower
        or "evidence-based" in scope_lower
    ):
        codes.append(_RC_RFP_EFFICACY)

    if (
        "outcomes-based" in scope_lower
        or "performance guarantee" in scope_lower
        or "risk share" in scope_lower
    ):
        codes.append(_RC_RFP_OUTCOMES_BASED)

    return codes


# ---------------------------------------------------------------------------
# Public mapping function
# ---------------------------------------------------------------------------


def posting_to_finding(posting: dict[str, Any]) -> dict[str, Any]:
    """Convert a procurement portal posting to a finding dict.

    All procurement RFPs are urgency ``"hot"`` — the variable is whether
    ``RFP_DEADLINE_CRITICAL`` is also in the reason codes.

    Parameters
    ----------
    posting:
        Dict from ``fetch_portal_postings`` with keys: portal_id, state,
        rfp_id, title, agency, posted_date, due_date, source_url,
        description, scope_text.

    Returns
    -------
    dict[str, Any]
        Finding dict ready for the emit_signals payload.
    """
    state = posting.get("state", "").upper()
    # Bonfire (and future platform adapters) supply a precise "district_id" key;
    # fall back to the generic STATE_<XX> identifier for statewide portals.
    district_id: str = posting.get("district_id") or f"STATE_{state}"

    evidence_title = posting.get("title", "")
    evidence_desc = posting.get("description", "")[:300]
    evidence = f"{evidence_title}. {evidence_desc}".strip()

    dtc = days_to_close(posting.get("due_date", ""))

    return {
        "sourceType": "procurement_portal",
        "discoveredBy": "procurement_scout",
        "districtId": district_id,
        "reasonCodes": _reason_codes(posting),
        "urgency": "hot",
        "evidence": evidence,
        "metadata": {
            "portal_id": posting.get("portal_id", ""),
            "state": posting.get("state", ""),
            "rfp_id": posting.get("rfp_id", ""),
            "source_url": posting.get("source_url", ""),
            "posted_date": posting.get("posted_date", ""),
            "due_date": posting.get("due_date", ""),
            "days_to_close": dtc,
            "agency": posting.get("agency", ""),
        },
    }
