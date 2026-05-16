"""Mapping helpers: convert board meeting items into scout finding dicts.

This module is purely deterministic — no I/O, no async.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Relevance filter
# ---------------------------------------------------------------------------

LITERACY_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "obc",
    "outcomes-based",
    "curriculum",
    "assessment",
    "tutoring",
    "vendor",
    "rfp",
    "superintendent",
    "transition",
    "esser",
]

# ---------------------------------------------------------------------------
# Reason code constants
# ---------------------------------------------------------------------------

_RC_BOARD_RFP_AUTHORIZATION = "BOARD_RFP_AUTHORIZATION"
_RC_BOARD_OBC_RFP_APPROVED = "BOARD_OBC_RFP_APPROVED"
_RC_SUPERINTENDENT_TRANSITION = "SUPERINTENDENT_TRANSITION"
_RC_BOARD_RFP_PENDING = "BOARD_RFP_AUTHORIZATION"  # same code, different urgency
_RC_BOARD_OBC_DISCUSSION = "BOARD_OBC_DISCUSSION"
_RC_BOARD_VENDOR_REVIEW = "BOARD_VENDOR_REVIEW"
_RC_BOARD_VENDOR_ACCOUNTABILITY = "BOARD_VENDOR_ACCOUNTABILITY"
_RC_BOARD_BUDGET_PRESSURE = "BOARD_BUDGET_PRESSURE"
_RC_ESSER_CLIFF = "ESSER_CLIFF_REFERENCE"
_RC_BOARD_LITERACY_CURRICULUM = "BOARD_LITERACY_CURRICULUM_REVIEW"


def _is_relevant(text: str) -> bool:
    """Return True if *text* contains at least one literacy keyword."""
    lower = text.lower()
    return any(kw in lower for kw in LITERACY_KEYWORDS)


def _classify(combined: str) -> tuple[str, str]:
    """Return (reason_code, urgency) for *combined* text (title + body).

    Checks conditions in priority order — first match wins.
    """
    lower = combined.lower()

    # Hot signals first.
    if "rfp" in lower and ("approved" in lower or "authorization" in lower):
        return _RC_BOARD_RFP_AUTHORIZATION, "hot"

    if ("obc" in lower or "outcomes-based" in lower) and "approved" in lower:
        return _RC_BOARD_OBC_RFP_APPROVED, "hot"

    if "superintendent" in lower and (
        "transition" in lower or "resign" in lower or "retire" in lower or "new" in lower
    ):
        return _RC_SUPERINTENDENT_TRANSITION, "hot"

    # Standard signals.
    if "rfp" in lower or "procurement" in lower:
        return _RC_BOARD_RFP_AUTHORIZATION, "standard"

    if "obc" in lower or "outcomes-based" in lower:
        return _RC_BOARD_OBC_DISCUSSION, "standard"

    if "vendor" in lower and "review" in lower:
        return _RC_BOARD_VENDOR_REVIEW, "standard"

    if "vendor" in lower and "accountability" in lower:
        return _RC_BOARD_VENDOR_ACCOUNTABILITY, "standard"

    if "budget" in lower and ("cut" in lower or "pressure" in lower or "reduction" in lower):
        return _RC_BOARD_BUDGET_PRESSURE, "standard"

    if "esser" in lower:
        return _RC_ESSER_CLIFF, "standard"

    # Default — covers literacy / reading / curriculum / assessment / tutoring.
    return _RC_BOARD_LITERACY_CURRICULUM, "standard"


def _build_evidence(title: str, text: str) -> str:
    """Return the item title plus up to the first two sentences of *text*."""
    sentences = [s.strip() for s in text.split(".") if s.strip()]
    excerpt_parts = sentences[:2]
    excerpt = ". ".join(excerpt_parts) + ("." if excerpt_parts else "")
    parts = [p for p in (title.strip(), excerpt.strip()) if p]
    return " ".join(parts)


def _detect_source_type(district: dict[str, Any], item: dict[str, Any]) -> str:
    """Infer which source type produced this item from context."""
    url: str = item.get("source_url", "")
    if "boarddocs" in url.lower():
        return "boarddocs"
    if "granicus" in url.lower():
        return "granicus"
    district_site: str | None = district.get("district_site_url")
    if district_site and url.startswith(district_site.split("//")[-1].split("/")[0]):
        return "district_site"
    # Fall back to examining which URLs are set on the district config.
    if district.get("boarddocs_url") and url == district["boarddocs_url"]:
        return "boarddocs"
    if district.get("granicus_url") and url == district["granicus_url"]:
        return "granicus"
    return "district_site"


def meeting_item_to_finding(
    item: dict[str, Any],
    district: dict[str, Any],
) -> dict[str, Any] | None:
    """Map a board meeting item to a finding dict.

    Parameters
    ----------
    item:
        Meeting item dict with keys: title, date, source_url, text,
        speaker_attribution.
    district:
        District config dict from the watch list.

    Returns
    -------
    dict | None
        Finding dict, or ``None`` when the item contains no literacy keywords.
    """
    title: str = item.get("title", "")
    text: str = item.get("text", "")
    combined = f"{title} {text}"

    if not _is_relevant(combined):
        return None

    reason_code, urgency = _classify(combined)

    date: str = item.get("date", "")
    speaker: str | None = item.get("speaker_attribution")
    if speaker is None:
        speaker = f"Unknown speaker, {date} board meeting"

    source_type = _detect_source_type(district, item)

    return {
        "sourceType": "board_minutes",
        "discoveredBy": "board_minutes_scout",
        "districtId": district["district_id"],
        "reasonCodes": [reason_code],
        "urgency": urgency,
        "evidence": _build_evidence(title, text),
        "metadata": {
            "district_id": district["district_id"],
            "state": district["state"],
            "source_url": item.get("source_url", ""),
            "meeting_date": date,
            "speaker_attribution": speaker,
            "source_type": source_type,
        },
    }
