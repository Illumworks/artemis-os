"""Mapping helpers — convert raw state DoE source items into scout finding dicts.

This module is purely deterministic: no I/O, no async, no side effects.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Reason code constants
# ---------------------------------------------------------------------------

STATE_GUIDANCE_ISSUED = "STATE_GUIDANCE_ISSUED"
STATE_MANDATE_ISSUED = "STATE_MANDATE_ISSUED"
GUBERNATORIAL_EO_LITERACY = "GUBERNATORIAL_EO_LITERACY"
STATE_OBC_LEGISLATION = "STATE_OBC_LEGISLATION"
STATE_DYSLEXIA_MANDATE = "STATE_DYSLEXIA_MANDATE"
STATE_BILITERACY_INITIATIVE = "STATE_BILITERACY_INITIATIVE"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _combined_text(item: dict[str, Any]) -> str:
    """Return a single lowercase string of all text fields for keyword matching."""
    parts = [
        str(item.get("title", "")),
        str(item.get("summary", "")),
        str(item.get("snippet", "")),
        str(item.get("text", "")),
    ]
    return " ".join(parts).lower()


def _is_eo(text: str) -> bool:
    """Return True when the text signals an executive order."""
    return (
        "executive order" in text
        or " eo " in text
        or text.startswith("eo ")
        or text.endswith(" eo")
        or "gubernatorial" in text
    )


def _is_mandate(text: str) -> bool:
    """Return True when the text signals a binding mandate (not just 'recommended')."""
    if "mandate" not in text:
        return False
    # Exclude "recommended" mandates — they're guidance, not binding
    return "recommended" not in text


def _reason_codes_and_urgency(item: dict[str, Any]) -> tuple[list[str], str]:
    """Derive reason codes and urgency tier from item text.

    Returns
    -------
    tuple[list[str], str]
        (reason_codes, urgency)
    """
    text = _combined_text(item)
    codes: list[str] = []
    urgency = "enrichment"

    # --- Primary classification (sets urgency) ---
    if _is_eo(text):
        codes.append(GUBERNATORIAL_EO_LITERACY)
        urgency = "hot"
    elif _is_mandate(text):
        codes.append(STATE_MANDATE_ISSUED)
        urgency = "hot"
    elif "guidance" in text:
        codes.append(STATE_GUIDANCE_ISSUED)
        urgency = "standard"

    # --- Topic tags (additive, do not override urgency) ---
    if "dyslexia" in text:
        codes.append(STATE_DYSLEXIA_MANDATE)
    if "biliteracy" in text or "bi-literacy" in text:
        codes.append(STATE_BILITERACY_INITIATIVE)
    if "outcomes-based" in text or " obc " in text or bool(re.search(r"\bobc\b", text)):
        codes.append(STATE_OBC_LEGISLATION)

    # --- Default when no primary classification matched ---
    if not codes:
        codes.append(STATE_GUIDANCE_ISSUED)

    return codes, urgency


def _build_evidence(item: dict[str, Any]) -> str:
    """Construct a short evidence string: title + first 2 sentences of body text."""
    title = str(item.get("title", "")).strip()

    body_raw = (
        str(item.get("summary", "")) or str(item.get("snippet", "")) or str(item.get("text", ""))
    ).strip()

    sentences = [s.strip() for s in body_raw.split(".") if s.strip()]
    excerpt_parts = sentences[:2]
    excerpt = ". ".join(excerpt_parts)
    if excerpt:
        excerpt += "."

    parts = [p for p in [title, excerpt] if p]
    return " ".join(parts)


def _source_type_from_item(item: dict[str, Any]) -> str:
    """Return the sourceType string for a raw item dict.

    Governor RSS items get ``"governor_press"``; everything else gets
    ``"state_doe"``.
    """
    raw_source = str(item.get("_source_type", ""))
    if raw_source == "governor_rss":
        return "governor_press"
    return "state_doe"


def _source_meta_type(item: dict[str, Any]) -> str:
    """Return the metadata source_type label (doe_rss, doe_html, state_board, …)."""
    return str(item.get("_source_type", "unknown"))


def _item_url(item: dict[str, Any]) -> str:
    """Extract a canonical URL from an item dict, checking multiple keys."""
    return str(item.get("link") or item.get("source_url") or "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def item_to_finding(item: dict[str, Any], state: str) -> dict[str, Any]:
    """Convert a raw source item dict into a scout finding dict.

    Parameters
    ----------
    item:
        Dict returned by one of the fetch helpers in ``sources.py``.
        Expected keys vary by source type but always include ``title``.
    state:
        Two-letter state abbreviation (e.g. ``"FL"``).

    Returns
    -------
    dict[str, Any]
        Finding dict ready for inclusion in the emit_signals payload.
    """
    reason_codes, urgency = _reason_codes_and_urgency(item)
    source_url = _item_url(item)

    return {
        "sourceType": _source_type_from_item(item),
        "discoveredBy": "state_doe_scout",
        "districtId": f"STATE_{state.upper()}",
        "reasonCodes": reason_codes,
        "urgency": urgency,
        "evidence": _build_evidence(item),
        "metadata": {
            "state": state.upper(),
            "source_url": source_url,
            "source_type": _source_meta_type(item),
        },
    }
