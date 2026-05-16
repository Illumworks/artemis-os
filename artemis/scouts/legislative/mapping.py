"""Mapping helpers: convert a LegiScan Bill into a scout finding dict.

This module is purely deterministic — no I/O, no async.
"""

from __future__ import annotations

from typing import Any

from artemis.scouts.legislative.client import (
    STATUS_ENGROSSED,
    STATUS_ENROLLED,
    STATUS_INTRODUCED,
    STATUS_PASSED,
    Bill,
)

# ---------------------------------------------------------------------------
# Reason code constants
# ---------------------------------------------------------------------------

_RC_BILL_INTRODUCED = "BILL_INTRODUCED"
_RC_BILL_PASSED_CHAMBER = "BILL_PASSED_CHAMBER"
_RC_BILL_ENACTED = "BILL_ENACTED"
_RC_STATE_OBC = "STATE_OBC_LEGISLATION"
_RC_STATE_DYSLEXIA = "STATE_DYSLEXIA_MANDATE"
_RC_STATE_BILITERACY = "STATE_BILITERACY_INITIATIVE"


def _status_reason_code(status: int) -> str:
    """Return the stage-based reason code for a LegiScan status integer."""
    if status >= STATUS_PASSED:  # 4, 5, 6 — passed, vetoed, failed all past "passed"
        return _RC_BILL_ENACTED
    if status in (STATUS_ENGROSSED, STATUS_ENROLLED):  # 2, 3
        return _RC_BILL_PASSED_CHAMBER
    return _RC_BILL_INTRODUCED  # default / status == 1


def _content_reason_codes(text: str) -> list[str]:
    """Return topic-specific reason codes based on keyword presence in *text*."""
    lower = text.lower()
    codes: list[str] = []
    if (
        "outcomes-based" in lower
        or " obc " in lower
        or "obc" in lower.split()
        or "performance contract" in lower
    ):
        codes.append(_RC_STATE_OBC)
    if "dyslexia" in lower:
        codes.append(_RC_STATE_DYSLEXIA)
    if "biliteracy" in lower or "bi-literacy" in lower:
        codes.append(_RC_STATE_BILITERACY)
    return codes


def _urgency(status: int) -> str:
    """Return urgency tier string for a LegiScan status integer."""
    if status >= STATUS_ENROLLED:  # 3+ — passed a chamber
        return "hot"
    if status in (STATUS_INTRODUCED, STATUS_ENGROSSED):  # 1, 2
        return "standard"
    return "enrichment"


def _build_evidence(bill: Bill) -> str:
    """Construct a 1–3 sentence verbatim evidence snippet."""
    parts: list[str] = []
    if bill.title:
        parts.append(bill.title.strip())
    if bill.description:
        # Keep only enough to stay within 3 sentences
        sentences = [s.strip() for s in bill.description.split(".") if s.strip()]
        remaining_sentences = min(2, len(sentences))
        excerpt = ". ".join(sentences[:remaining_sentences])
        if excerpt:
            parts.append(excerpt + ".")
    return " ".join(parts)


def bill_to_finding(bill: Bill, state: str) -> dict[str, Any]:
    """Convert a full LegiScan Bill into a scout finding dict.

    Parameters
    ----------
    bill:
        Fully-fetched Bill model from LegiScan.
    state:
        Two-letter state abbreviation used for the search that produced this bill.

    Returns
    -------
    dict[str, Any]
        Finding dict ready to be included in the emit_signals payload.
    """
    combined_text = f"{bill.title} {bill.description}"

    reason_codes: list[str] = [_status_reason_code(bill.status)]
    reason_codes.extend(_content_reason_codes(combined_text))

    return {
        "sourceType": "legiscan",
        "discoveredBy": "legislative_scout",
        "districtId": f"STATE_{state.upper()}",
        "reasonCodes": reason_codes,
        "urgency": _urgency(bill.status),
        "evidence": _build_evidence(bill),
        "metadata": {
            "bill_id": bill.bill_id,
            "bill_number": bill.bill_number,
            "state": state.upper(),
            "status_code": bill.status,
            "last_action": bill.last_action,
        },
    }
