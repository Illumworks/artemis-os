"""Mapping helpers: convert board meeting items into scout finding dicts.

This module is purely deterministic — no I/O, no async.

Reason codes emitted here match the canonical registry in
docs/marketing-ops-v1/schemas/reason-codes.md.  The mapping targets
PRE-RFP-INTENT signals that surface 6–18 months before a formal RFP posts.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Relevance filter — must contain at least one of these to be worth classifying.
#
# Keywords are intentionally focused on genuine literacy/curriculum/procurement
# content.  Generic words like "assessment" or "curriculum" are kept only when
# they are strong enough to justify the scan cost; broad terms like
# "superintendent" that also match routine agenda items are NOT included here
# (the LLM path handles leader-transition signals separately via
# LEADER_TRANSITION_FORMAL from the leadership_transition scout).
# ---------------------------------------------------------------------------

LITERACY_KEYWORDS: list[str] = [
    # Core literacy / reading
    "literacy",
    "reading",
    "dyslexia",
    "phonics",
    "structured literacy",
    "science of reading",
    "foundational literacy",
    "language arts",
    "ela ",
    "ela,",
    "ela.",
    # Curriculum / program adoption
    "curriculum",
    "instructional materials",
    "reading program",
    "curriculum adoption",
    "ela adoption",
    "reading materials",
    "supplemental curriculum",
    # Tutoring / intervention
    "tutoring",
    "intervention",
    "mtss",
    "tier 2",
    "tier 3",
    "high-impact tutoring",
    "hit program",
    # Vendor / procurement
    "vendor",
    "rfp",
    "procurement",
    "iready",
    "lexia",
    "amplify",
    # Texas policy anchors
    "hb 1416",
    "hb1416",
    "hb 3",
    "hb3",
    "elia",
    "adsy",
    "tutoring waiver",
    "tea waiver",
    # Proficiency / strategic
    "proficiency",
    "achievement gap",
    "strategic plan",
    "literacy initiative",
    "reading initiative",
    "dual language",
    "bilingual",
    "dll",
    # Screen time
    "screen time",
    "ed tech time",
    # ESSER (kept for context)
    "esser",
    # OBC
    "obc",
    "outcomes-based",
]

# ---------------------------------------------------------------------------
# Canonical reason code constants (from decisions/campaign-signal-spec-v1.md)
# ---------------------------------------------------------------------------

# Hot signals
_RC_TX_HB1416_WAIVER = "TX_HB1416_WAIVER"
_RC_TX_HB3_DYSLEXIA = "TX_HB3_DYSLEXIA_COMPLIANCE"
_RC_PROCUREMENT_RFP = "PROCUREMENT_LITERACY_RFP"

# Standard signals — pre-RFP intent
_RC_PROCUREMENT_ELA = "PROCUREMENT_ELA_ADOPTION"
_RC_VENDOR_DISSATISFACTION = "VENDOR_DISSATISFACTION"
_RC_DISTRICT_STRATEGIC_LIT = "DISTRICT_STRATEGIC_LITERACY"
_RC_DISTRICT_PROF_GAP = "DISTRICT_PROFICIENCY_GAP"
_RC_DISTRICT_MTSS_STRAIN = "DISTRICT_MTSS_STRAIN"
_RC_DISTRICT_DLL = "DISTRICT_DLL_EXPANSION"
_RC_POLICY_LIT_MANDATE = "POLICY_LIT_MANDATE"
_RC_POLICY_EDTECH = "POLICY_EDTECH_TIME_LIMIT"

# Enrichment
_RC_FUNDING_HB2_ELIA = "FUNDING_HB2_ELIA"

# Fallback — generic literacy curriculum discussion
_RC_DISTRICT_STRATEGIC_LIT_FALLBACK = "DISTRICT_STRATEGIC_LITERACY"


def _is_relevant(text: str) -> bool:
    """Return True if *text* contains at least one literacy keyword."""
    lower = text.lower()
    return any(kw in lower for kw in LITERACY_KEYWORDS)


def _classify(combined: str) -> tuple[str, str]:
    """Return (reason_code, urgency) for *combined* text (title + body).

    Checks conditions in priority order — first match wins.  Codes and
    urgency tiers match the canonical spec (decisions/campaign-signal-spec-v1.md).
    """
    lower = combined.lower()

    # -----------------------------------------------------------------------
    # Hot signals first — direct buying/substitution signals
    # -----------------------------------------------------------------------

    # TX HB 1416 tutoring waiver (any mention = hot, Amira is TEA-approved)
    if "hb 1416" in lower or "hb1416" in lower or "tutoring waiver" in lower:
        return _RC_TX_HB1416_WAIVER, "hot"

    # ADSY (Additional Days School Year) when scoped to literacy / reading
    if "adsy" in lower and any(kw in lower for kw in ("reading", "literacy", "tutoring")):
        return _RC_TX_HB1416_WAIVER, "hot"

    # TX HB 3 dyslexia compliance
    if ("hb 3" in lower or "hb3" in lower) and (
        "dyslexia" in lower or "reporting" in lower or "compliance" in lower
    ):
        return _RC_TX_HB3_DYSLEXIA, "hot"

    # Formal RFP authorization for literacy/reading/assessment/tutoring
    if "rfp" in lower and any(
        kw in lower
        for kw in ("literacy", "reading", "curriculum", "assessment", "tutoring", "ela")
    ):
        if any(kw in lower for kw in ("approved", "authorization", "authorize")):
            return _RC_PROCUREMENT_RFP, "hot"
        return _RC_PROCUREMENT_RFP, "standard"

    # Named competitor + negative action → hot VENDOR_DISSATISFACTION
    # (vendor non-renewal/replacement without the generic "vendor" word in title)
    _named_vendors = ("iready", "lexia", "amplify", "imagine learning", "ucsf multitudes")
    _negative_actions = ("non-renewal", "non renewal", "not renew", "replace", "terminate", "end contract")
    if any(v in lower for v in _named_vendors) and any(a in lower for a in _negative_actions):
        return _RC_VENDOR_DISSATISFACTION, "hot"

    # Vendor non-renewal vote → hot VENDOR_DISSATISFACTION
    if "vendor" in lower and any(
        kw in lower for kw in ("non-renewal", "non renewal", "not renew", "replace", "terminate")
    ):
        return _RC_VENDOR_DISSATISFACTION, "hot"

    # -----------------------------------------------------------------------
    # Standard pre-RFP intent signals
    # -----------------------------------------------------------------------

    # Vendor review / dissatisfaction (check BEFORE ELA adoption so "reading curriculum"
    # in a vendor-review context doesn't get grabbed by the ELA adoption path)
    if "vendor" in lower and any(
        kw in lower
        for kw in (
            "review",
            "evaluation",
            "efficacy",
            "renewal",
            "iready",
            "lexia",
            "amplify",
            "accountability",
        )
    ):
        return _RC_VENDOR_DISSATISFACTION, "standard"

    # OBC (outcomes-based contracting) discussion or approval
    if "obc" in lower or "outcomes-based" in lower:
        return _RC_PROCUREMENT_ELA, "standard"

    # ESSER cliff / fund expiration — enrichment context. Check BEFORE generic ELA/reading
    # phrases so "esser ... reading programs" doesn't false-positive as ELA adoption.
    if "esser" in lower:
        return _RC_DISTRICT_STRATEGIC_LIT_FALLBACK, "enrichment"

    # Curriculum / ELA materials purchase / adoption committee — PROCUREMENT_ELA_ADOPTION
    if any(
        phrase in lower
        for phrase in (
            "ela adoption",
            "curriculum adoption",
            "instructional materials",
            "reading materials",
            "reading program",
            "supplemental curriculum",
            "reading curriculum",
            "language arts adoption",
            "ela materials",
            "core materials",
        )
    ):
        return _RC_PROCUREMENT_ELA, "standard"

    # Strategic literacy plan / reading initiative
    if any(
        phrase in lower
        for phrase in (
            "strategic plan",
            "literacy goals",
            "reading initiative",
            "literacy initiative",
            "science of reading",
            "structured literacy",
            "foundational literacy",
            "k-3 reading",
            "k3 reading",
        )
    ):
        return _RC_DISTRICT_STRATEGIC_LIT, "standard"

    # Proficiency gap / assessment data
    if any(
        phrase in lower
        for phrase in (
            "proficiency",
            "achievement gap",
            "reading scores",
            "literacy outcomes",
            "below grade level",
            "naep",
            "state assessments",
            "student performance",
        )
    ) and any(kw in lower for kw in ("reading", "literacy", "ela", "language arts")):
        return _RC_DISTRICT_PROF_GAP, "standard"

    # MTSS / intervention staffing
    if any(
        phrase in lower
        for phrase in (
            "mtss",
            "tier 2",
            "tier 3",
            "intervention staffing",
            "reading specialist",
            "literacy coach",
            "intervention capacity",
        )
    ):
        return _RC_DISTRICT_MTSS_STRAIN, "standard"

    # Dual language / bilingual expansion
    if any(
        phrase in lower
        for phrase in ("dual language", "bilingual", "dll", "biliteracy", "multilingual")
    ) and any(kw in lower for kw in ("expand", "program", "addition", "new", "launch")):
        return _RC_DISTRICT_DLL, "standard"

    # Screen-time / ed-tech limits
    if "screen time" in lower or "ed tech time" in lower:
        return _RC_POLICY_EDTECH, "standard"

    # HB 2 ELIA (TX enrichment context)
    if any(phrase in lower for phrase in ("hb 2", "elia", "early literacy intervention allotment")):
        return _RC_FUNDING_HB2_ELIA, "enrichment"

    # Generic tutoring / high-impact tutoring
    if any(phrase in lower for phrase in ("tutoring", "high-impact tutoring", "hit program")):
        return _RC_DISTRICT_MTSS_STRAIN, "standard"

    # General curriculum / literacy discussion (fallback)
    return _RC_DISTRICT_STRATEGIC_LIT_FALLBACK, "standard"


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
        speaker = f"Board agenda item, {date} board meeting"

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
