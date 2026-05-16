"""Mapping helpers: convert news/board/DoE items into scout finding dicts.

This module is purely deterministic — no I/O, no async.

Three public functions:
- article_to_finding   — maps a newsapi.org article
- board_item_to_finding — maps a board minutes item
- doe_item_to_finding  — maps a state DoE press item
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Reason code constants
# ---------------------------------------------------------------------------

_RC_BOARD_RFP_AUTHORIZATION = "BOARD_RFP_AUTHORIZATION"
_RC_BOARD_LITERACY_CURRICULUM = "BOARD_LITERACY_CURRICULUM_REVIEW"
_RC_SUPERINTENDENT_TRANSITION = "SUPERINTENDENT_TRANSITION"
_RC_GUBERNATORIAL_EO = "GUBERNATORIAL_EO_LITERACY"
_RC_STATE_MANDATE = "STATE_MANDATE_ISSUED"
_RC_STATE_GUIDANCE = "STATE_GUIDANCE_ISSUED"
_RC_BOARD_OBC_DISCUSSION = "BOARD_OBC_DISCUSSION"
_RC_STATE_DYSLEXIA = "STATE_DYSLEXIA_MANDATE"
_RC_ESSER_CLIFF = "ESSER_CLIFF_REFERENCE"

# ---------------------------------------------------------------------------
# Shared classification helpers
# ---------------------------------------------------------------------------


def _classify(combined: str) -> tuple[list[str], str]:
    """Return (reason_codes, urgency) for *combined* text.

    Checks conditions in priority order. Multiple reason codes may apply;
    urgency is 'hot' if any hot rule fires, 'standard' otherwise.
    """
    lower = combined.lower()
    reason_codes: list[str] = []
    urgency = "standard"

    # --- Hot signals (checked first) ---

    if "rfp" in lower and ("approved" in lower or "authorized" in lower):
        reason_codes.append(_RC_BOARD_RFP_AUTHORIZATION)
        urgency = "hot"

    elif "rfp" in lower or "procurement" in lower:
        reason_codes.append(_RC_BOARD_RFP_AUTHORIZATION)

    if "board vote" in lower or "board approved" in lower or "passed" in lower:
        if _RC_BOARD_LITERACY_CURRICULUM not in reason_codes:
            reason_codes.append(_RC_BOARD_LITERACY_CURRICULUM)
        urgency = "hot"

    if "superintendent" in lower and (
        "hired" in lower or "named" in lower or "transition" in lower or "resign" in lower
    ):
        if _RC_SUPERINTENDENT_TRANSITION not in reason_codes:
            reason_codes.append(_RC_SUPERINTENDENT_TRANSITION)
        urgency = "hot"

    if ("gubernatorial" in lower or "executive order" in lower) or (
        "governor" in lower and ("literacy" in lower or "mandate" in lower)
    ):
        if _RC_GUBERNATORIAL_EO not in reason_codes:
            reason_codes.append(_RC_GUBERNATORIAL_EO)
        urgency = "hot"

    if "state mandate" in lower or "mandate issued" in lower:
        if _RC_STATE_MANDATE not in reason_codes:
            reason_codes.append(_RC_STATE_MANDATE)
        urgency = "hot"

    # --- Standard signals (only added if not already covered) ---

    if "guidance" in lower and _RC_STATE_GUIDANCE not in reason_codes:
        reason_codes.append(_RC_STATE_GUIDANCE)

    if (
        "obc" in lower or "outcomes-based" in lower
    ) and _RC_BOARD_OBC_DISCUSSION not in reason_codes:
        reason_codes.append(_RC_BOARD_OBC_DISCUSSION)

    if "dyslexia" in lower and _RC_STATE_DYSLEXIA not in reason_codes:
        reason_codes.append(_RC_STATE_DYSLEXIA)

    if "esser" in lower and _RC_ESSER_CLIFF not in reason_codes:
        reason_codes.append(_RC_ESSER_CLIFF)

    # Default fallback: ensure at least one reason code
    if not reason_codes:
        reason_codes.append(_RC_BOARD_LITERACY_CURRICULUM)

    return reason_codes, urgency


# ---------------------------------------------------------------------------
# Relevance check (shared keyword gate)
# ---------------------------------------------------------------------------

_LITERACY_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "biliteracy",
    "obc",
    "outcomes-based",
    "curriculum",
    "assessment",
    "tutoring",
    "superintendent",
    "rfp",
    "esser",
    "board vote",
    "board approved",
    "guidance",
    "mandate",
    "governor",
    "gubernatorial",
    "procurement",
    "passed",
]


def _is_relevant(text: str) -> bool:
    """Return True if *text* contains at least one literacy-related keyword."""
    lower = text.lower()
    return any(kw in lower for kw in _LITERACY_KEYWORDS)


# ---------------------------------------------------------------------------
# Public mappers
# ---------------------------------------------------------------------------


def article_to_finding(
    article: dict[str, Any],
    district: dict[str, Any],
) -> dict[str, Any] | None:
    """Map a news article to a finding dict.

    Returns None if the article is not literacy-relevant.
    """
    title: str = article.get("title") or ""
    description: str = article.get("description") or ""
    combined = f"{title} {description}"

    if not _is_relevant(combined):
        return None

    reason_codes, urgency = _classify(combined)

    return {
        "sourceType": "news_article",
        "discoveredBy": "regional_news_scout",
        "districtId": district["district_id"],
        "reasonCodes": reason_codes,
        "urgency": urgency,
        "evidence": f"{title}. {description[:300]}",
        "metadata": {
            "district_id": district["district_id"],
            "state": district["state"],
            "source_url": article.get("url") or "",
            "published_at": article.get("published_at") or "",
            "source_name": article.get("source_name") or "",
            "source_type": "news_article",
        },
    }


def board_item_to_finding(
    item: dict[str, Any],
    district: dict[str, Any],
) -> dict[str, Any] | None:
    """Map a board minutes item to a finding dict.

    Returns None if the item is not literacy-relevant.
    """
    title: str = item.get("title") or ""
    text: str = item.get("text") or ""
    combined = f"{title} {text}"

    if not _is_relevant(combined):
        return None

    reason_codes, urgency = _classify(combined)

    return {
        "sourceType": "board_minutes",
        "discoveredBy": "regional_news_scout",
        "districtId": district["district_id"],
        "reasonCodes": reason_codes,
        "urgency": urgency,
        "evidence": f"{title}. {text[:300]}",
        "metadata": {
            "district_id": district["district_id"],
            "state": district["state"],
            "source_url": item.get("source_url") or "",
            "published_at": item.get("date") or "",
            "source_name": district.get("district_name") or "",
            "source_type": "board_minutes",
        },
    }


def doe_item_to_finding(
    item: dict[str, Any],
    district: dict[str, Any],
) -> dict[str, Any] | None:
    """Map a state DoE press item to a finding dict.

    Returns None if the item is not literacy-relevant.
    """
    title: str = item.get("title") or ""
    summary: str = item.get("summary") or ""
    combined = f"{title} {summary}"

    if not _is_relevant(combined):
        return None

    reason_codes, urgency = _classify(combined)

    return {
        "sourceType": "state_doe",
        "discoveredBy": "regional_news_scout",
        "districtId": district["district_id"],
        "reasonCodes": reason_codes,
        "urgency": urgency,
        "evidence": f"{title}. {summary[:300]}",
        "metadata": {
            "district_id": district["district_id"],
            "state": district["state"],
            "source_url": item.get("link") or "",
            "published_at": item.get("published") or "",
            "source_name": f"{district.get('state', '')} DoE",
            "source_type": "state_doe",
        },
    }
