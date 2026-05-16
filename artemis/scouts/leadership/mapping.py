"""Pure deterministic mapping: raw items → leadership transition finding dicts.

All functions here are side-effect free and fully deterministic — no network
calls, no logging, no external state.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Keyword sets
# ---------------------------------------------------------------------------

TRANSITION_KEYWORDS_HOT: list[str] = [
    "formally hired",
    "officially named",
    "superintendent hired",
    "board approved",
    "voted to hire",
    "confirmed",
    "officially appointed",
    "accepted the position",
]

# ---------------------------------------------------------------------------
# Reason-code classifier
# ---------------------------------------------------------------------------


def classify_transition_stage(text: str) -> str:
    """Return the most specific reason code based on *text* content.

    Checks are performed in descending specificity:

    1. Hot hire keywords → ``SUPE_FORMAL_HIRE``
    2. "interim" → ``SUPE_INTERIM_NAMED``
    3. Search-related keywords → ``SUPE_SEARCH_ANNOUNCED``
    4. Senior-leader keywords → ``SENIOR_LEADER_TRANSITION``
    5. "superintendent" + "transition" together → ``SUPERINTENDENT_TRANSITION``
    6. Default fallback → ``SUPERINTENDENT_TRANSITION``
    """
    lower = text.lower()

    # 1. Formal hire (hot)
    if any(kw in lower for kw in TRANSITION_KEYWORDS_HOT):
        return "SUPE_FORMAL_HIRE"

    # 2. Interim appointment
    if "interim" in lower:
        return "SUPE_INTERIM_NAMED"

    # 3. Search announced
    if (
        "search committee" in lower
        or "superintendent search" in lower
        or "search underway" in lower
    ):
        return "SUPE_SEARCH_ANNOUNCED"

    # 4. Senior leader (non-superintendent)
    if (
        "curriculum director" in lower
        or "assistant superintendent" in lower
        or "principal" in lower
    ):
        return "SENIOR_LEADER_TRANSITION"

    # 5. General superintendent transition
    if "superintendent" in lower and "transition" in lower:
        return "SUPERINTENDENT_TRANSITION"

    # 6. Default
    return "SUPERINTENDENT_TRANSITION"


# ---------------------------------------------------------------------------
# Item → finding mapper
# ---------------------------------------------------------------------------


def item_to_transition_finding(
    item: dict[str, Any],
    district: dict[str, Any],
    source_type: str,
    source_count: int,
) -> dict[str, Any]:
    """Convert a raw item dict to a leadership transition finding dict.

    Parameters
    ----------
    item:
        Raw item from any source (board_minutes, state_doe, news_article).
        Expected keys: ``title``, ``text`` or ``snippet``, ``source_url`` or ``link``.
    district:
        Watch-list district entry with at minimum ``district_id`` and ``state``.
    source_type:
        One of ``"board_minutes"``, ``"state_doe"``, or ``"news_article"``.
    source_count:
        Number of distinct source types that produced a signal for this district.
        Stored in metadata for downstream confidence scoring.

    Returns
    -------
    dict
        A finding dict compatible with the ``POST /api/scouts/runs`` payload.
    """
    title: str = item.get("title") or ""
    body: str = item.get("text") or item.get("snippet") or item.get("summary") or ""
    combined_text = title + " " + body

    reason_code = classify_transition_stage(combined_text)
    urgency = "hot" if reason_code == "SUPE_FORMAL_HIRE" else "standard"

    evidence = title + ". " + body[:300]

    source_url: str = item.get("source_url") or item.get("link") or ""

    return {
        "sourceType": source_type,
        "discoveredBy": "leadership_transition_scout",
        "districtId": district["district_id"],
        "reasonCodes": [reason_code],
        "urgency": urgency,
        "evidence": evidence,
        "metadata": {
            "district_id": district["district_id"],
            "state": district["state"],
            "source_url": source_url,
            "source_type": source_type,
            "source_count": source_count,
            "districts_table_write": "TODO: write to districts table on SUPE_FORMAL_HIRE",
        },
    }
