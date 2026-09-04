"""Mapping helpers: convert a StarbridgeItem into a scout finding dict.

This module is purely deterministic — no I/O, no async.

NOTE: Reason-code heuristics and field names are based on assumed API shape.
All ambiguous assumptions are marked with ``# TODO: confirm with Starbridge team``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from artemis.scouts.starbridge.client import StarbridgeItem

# ---------------------------------------------------------------------------
# Reason code constants (from registry)
# ---------------------------------------------------------------------------

#: Josh's canonical registry (artemis.marketing.josh_spec) holds exactly 17 reason
#: codes. The four this module used to emit -- BILL_INTRODUCED, FEDERAL_GRANT_OPEN,
#: STATE_DYSLEXIA_MANDATE, STATE_OBC_LEGISLATION -- are in none of them. They were
#: invented alongside the rest of the fabricated integration, so every finding this
#: scout produced carried a code no downstream consumer recognises. A live run
#: labelled a Kansas *state* reading-screener RFP as FEDERAL_GRANT_OPEN.
_RC_LITERACY_RFP = "PROCUREMENT_LITERACY_RFP"
_RC_ELA_ADOPTION = "PROCUREMENT_ELA_ADOPTION"
_RC_APPROVED_LIST = "VENDOR_APPROVED_LIST"
_RC_LITERACY_GRANT = "FUNDING_LITERACY_GRANT"
_RC_LIT_MANDATE = "POLICY_LIT_MANDATE"
_RC_LEADER_FORMAL = "LEADER_TRANSITION_FORMAL"
_RC_STRATEGIC_LITERACY = "DISTRICT_STRATEGIC_LITERACY"

_LEGISLATION_TYPES: frozenset[str] = frozenset({"signal"})
_FUNDING_TYPES: frozenset[str] = frozenset({"rfp", "purchase"})

_DYSLEXIA_KEYWORDS: frozenset[str] = frozenset({"dyslexia", "dyslexic"})
_LITERACY_KEYWORDS: frozenset[str] = frozenset(
    {"literacy", "reading", "screener", "screening", "phonics", "intervention", "tutor"}
)
_ADOPTION_KEYWORDS: frozenset[str] = frozenset(
    {"instructional material", "curriculum", "adoption", "ela "}
)
_APPROVED_LIST_KEYWORDS: frozenset[str] = frozenset({"approved list", "vendor list", "hqim"})
_GRANT_KEYWORDS: frozenset[str] = frozenset({"grant", "funding", "award", "appropriation"})
_LEADER_KEYWORDS: frozenset[str] = frozenset(
    {"superintendent", "chief academic officer", "appointment", "resignation"}
)

_URGENCY_HOT_DAYS = 30
_URGENCY_STANDARD_DAYS = 90


def _reason_codes(item: StarbridgeItem) -> list[str]:
    """Map a signal onto Josh's registry, or onto nothing.

    Returning an empty list is a real outcome and the caller drops the finding.
    A signal we cannot classify is worth less than nothing once it carries a
    confident, wrong label: the previous version defaulted every unrecognised
    item to BILL_INTRODUCED, which is how a Kansas procurement notice became a
    federal grant.
    """
    text = f"{item.title or ''} {item.summary or ''}".lower()
    item_type = (item.item_type or "").lower()

    codes: list[str] = []

    if any(kw in text for kw in _APPROVED_LIST_KEYWORDS):
        codes.append(_RC_APPROVED_LIST)

    if item_type in _FUNDING_TYPES:
        # An RFP is procurement. Which kind depends on what is being bought.
        if any(kw in text for kw in _LITERACY_KEYWORDS):
            codes.append(_RC_LITERACY_RFP)
        if any(kw in text for kw in _ADOPTION_KEYWORDS):
            codes.append(_RC_ELA_ADOPTION)

    if any(kw in text for kw in _GRANT_KEYWORDS):
        codes.append(_RC_LITERACY_GRANT)

    if any(kw in text for kw in _DYSLEXIA_KEYWORDS):
        codes.append(_RC_LIT_MANDATE)

    if item_type == "meeting":
        if any(kw in text for kw in _LEADER_KEYWORDS):
            codes.append(_RC_LEADER_FORMAL)
        elif any(kw in text for kw in _LITERACY_KEYWORDS):
            codes.append(_RC_STRATEGIC_LITERACY)

    # Preserve order, drop repeats.
    return list(dict.fromkeys(codes))


def _urgency(deadline_date: str | None) -> str:
    """Return urgency tier string based on deadline proximity.

    Returns
    -------
    str
        ``"hot"`` if deadline within 30 days,
        ``"standard"`` if 30-90 days out,
        ``"enrichment"`` otherwise (no deadline falls here).
    """
    if not deadline_date:
        return "enrichment"

    try:
        # TODO: confirm with Starbridge team — deadline_date format
        deadline = date.fromisoformat(deadline_date[:10])
    except (ValueError, TypeError):
        _logger_fallback = "enrichment"
        return _logger_fallback

    today = datetime.utcnow().date()
    days_until = (deadline - today).days

    if days_until <= _URGENCY_HOT_DAYS:
        return "hot"
    if days_until <= _URGENCY_STANDARD_DAYS:
        return "standard"
    return "enrichment"


def _district_id(item: StarbridgeItem) -> str:
    """Identify the buyer, falling back to the state, then to national.

    Starbridge names the buyer on the row ("Kansas State Department of
    Education"), and that is far more use than STATE_NATIONAL -- which is what
    every single live finding was labelled, because `state` is never populated by
    the feed.
    """
    if item.buyer_name:
        return item.buyer_name.strip()
    if item.state:
        return f"STATE_{item.state.upper()}"
    return "STATE_NATIONAL"


def _evidence(item: StarbridgeItem) -> str:
    """Construct verbatim evidence string from item title and summary.

    Evidence is never paraphrased — only concatenation of raw API fields.
    """
    parts: list[str] = []
    if item.title:
        parts.append(item.title.strip())
    if item.summary:
        parts.append(item.summary.strip())
    return " ".join(parts)


def item_to_finding(item: StarbridgeItem) -> dict[str, Any]:
    """Convert a StarbridgeItem into a scout finding dict.

    Parameters
    ----------
    item:
        Parsed StarbridgeItem from the Starbridge API.

    Returns
    -------
    dict[str, Any]
        Finding dict ready to be included in the emit_signals payload.
    """
    return {
        "sourceType": "starbridge",
        "discoveredBy": "starbridge_researcher",
        "districtId": _district_id(item),
        "reasonCodes": _reason_codes(item),
        "urgency": _urgency(item.deadline_date),
        "evidence": _evidence(item),
        "metadata": {
            "item_id": item.item_id,
            "item_type": item.item_type,
            "bench_test_period": True,
        },
    }
