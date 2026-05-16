"""Pure mapping functions: LinkedIn posts → finding dicts.

No I/O, no network calls.  All logic is deterministic and testable in isolation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Campaign theme keywords that indicate a post is relevant to Amira's sales motion.
CAMPAIGN_THEME_KEYWORDS: list[str] = [
    "literacy",
    "reading",
    "dyslexia",
    "biliteracy",
    "obc",
    "outcomes-based",
    "curriculum",
    "assessment",
    "tutoring",
    "esser",
    "growth",
    "intervention",
    "vendor",
    "rfp",
    "contract",
    "selection",
    "pilot",
    "program review",
]

# Keywords that map to additional topical reason codes.
TOPICAL_REASON_CODES: dict[str, str] = {
    "esser": "ESSER_CLIFF_REFERENCE",
    "dyslexia": "STATE_DYSLEXIA_MANDATE",
    "biliteracy": "STATE_BILITERACY_INITIATIVE",
    "outcomes-based": "STATE_OBC_LEGISLATION",
    "obc": "STATE_OBC_LEGISLATION",
    "rfp": "BOARD_RFP_AUTHORIZATION",
    "vendor": "BOARD_VENDOR_REVIEW",
}

# Keywords that indicate action-level urgency (standard vs enrichment).
_STANDARD_URGENCY_KEYWORDS: frozenset[str] = frozenset(
    {
        "rfp",
        "vendor",
        "curriculum review",
        "selection process",
        "contract",
    }
)


def _week_key(posted_at: str) -> str:
    """Return ISO year-week string for dedup, e.g. '2026-W20'.

    Returns empty string when *posted_at* is empty or cannot be parsed.
    """
    try:
        dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        return dt.strftime("%Y-W%V")
    except Exception:
        return ""


def post_to_finding(
    post: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any] | None:
    """Map a LinkedIn post dict to a finding dict.

    Returns ``None`` when:
    - ``post["is_authored"]`` is ``False`` (reshares excluded — weak signal)
    - the post text contains no campaign theme keywords

    Parameters
    ----------
    post:
        Dict with keys: profile_id, post_id, text, posted_at, url, is_authored.
    profile:
        Watch-list profile dict with keys: profile_id, district_id, state, role, name.
    """
    # Reshares are a weak signal — skip them.
    if not post.get("is_authored", True):
        return None

    text_lower = post.get("text", "").lower()

    # Find matching campaign theme keywords.
    matched_keywords: list[str] = [kw for kw in CAMPAIGN_THEME_KEYWORDS if kw in text_lower]
    if not matched_keywords:
        return None

    # Build reason code list — LINKEDIN_LEADER_ENGAGEMENT is always present.
    reason_codes: list[str] = ["LINKEDIN_LEADER_ENGAGEMENT"]
    seen_topical: set[str] = set()
    for kw in matched_keywords:
        code = TOPICAL_REASON_CODES.get(kw)
        if code and code not in seen_topical:
            reason_codes.append(code)
            seen_topical.add(code)

    # Urgency: standard when an action keyword is present, enrichment otherwise.
    # LinkedIn rarely drives standalone action per spec — enrichment is the default.
    urgency = "enrichment"
    for action_kw in _STANDARD_URGENCY_KEYWORDS:
        if action_kw in text_lower:
            urgency = "standard"
            break

    evidence = post.get("text", "")[:300]

    return {
        "sourceType": "linkedin_post",
        "discoveredBy": "linkedin_observer",
        "districtId": profile["district_id"],
        "reasonCodes": reason_codes,
        "urgency": urgency,
        "evidence": evidence,
        "metadata": {
            "district_id": profile["district_id"],
            "state": profile["state"],
            "source_url": post.get("url", ""),
            "post_id": post.get("post_id", ""),
            "profile_id": post.get("profile_id", ""),
            "role": profile["role"],
            "posted_at": post.get("posted_at", ""),
            "contact_hints": {
                "name": profile["name"],
                "role": profile["role"],
                "linkedin_url": profile["profile_id"],
            },
        },
    }
