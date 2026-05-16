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

_RC_BILL_INTRODUCED = "BILL_INTRODUCED"
_RC_FEDERAL_GRANT_OPEN = "FEDERAL_GRANT_OPEN"
_RC_STATE_DYSLEXIA = "STATE_DYSLEXIA_MANDATE"
_RC_STATE_OBC = "STATE_OBC_LEGISLATION"

# TODO: confirm with Starbridge team — item_type values and their meanings
_LEGISLATION_TYPES: frozenset[str] = frozenset(
    {"bill", "legislation", "resolution", "amendment", "statute"}
)
_FUNDING_TYPES: frozenset[str] = frozenset({"grant", "funding", "award", "rfp", "solicitation"})

# Keywords that trigger additional reason codes (applied to title)
_DYSLEXIA_KEYWORDS: frozenset[str] = frozenset({"dyslexia", "dyslexic"})
_OBC_KEYWORDS: frozenset[str] = frozenset({"outcomes-based", "obc", "performance contract"})

_URGENCY_HOT_DAYS = 30
_URGENCY_STANDARD_DAYS = 90


def _reason_codes(item: StarbridgeItem) -> list[str]:
    """Determine reason codes from item_type and title keywords."""
    item_type_lower = (item.item_type or "").lower()
    title_lower = (item.title or "").lower()
    summary_lower = (item.summary or "").lower()
    combined_lower = f"{title_lower} {summary_lower}"

    codes: list[str] = []

    # Primary code based on item type
    if item_type_lower in _LEGISLATION_TYPES:
        codes.append(_RC_BILL_INTRODUCED)
    elif item_type_lower in _FUNDING_TYPES:
        codes.append(_RC_FEDERAL_GRANT_OPEN)
    else:
        # TODO: confirm with Starbridge team — default for unknown item_type
        codes.append(_RC_BILL_INTRODUCED)

    # Content-based supplemental codes
    if any(kw in combined_lower for kw in _DYSLEXIA_KEYWORDS):
        codes.append(_RC_STATE_DYSLEXIA)
    if any(kw in combined_lower for kw in _OBC_KEYWORDS):
        codes.append(_RC_STATE_OBC)

    return codes


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
    """Return the districtId string for a StarbridgeItem."""
    if item.state:
        return f"STATE_{item.state.upper()}"
    return "STATE_NATIONAL"  # TODO: confirm with Starbridge team — national fallback


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
