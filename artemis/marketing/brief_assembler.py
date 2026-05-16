"""Campaign Brief Assembler — deterministic, no LLM.

Port of Node's server/campaign-brief-assembler.js (149 lines).

Converts a campaign candidate + supporting data into an immutable Campaign Brief
matching the shape in MARKETING_WORKFLOW_BUILD_SPEC.md §3.5.

Guardrails:
 - No field is inferred or fabricated. Unknowns → null/[] with *Unavailable flags.
 - "undefined" and "null" strings must never appear in formatted output.
 - District, contacts, and audience tier data are always unavailable in Lite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# Priority → urgency tier mapping (mirrors Node)
# ─────────────────────────────────────────────────────────────────────────────

_PRIORITY_TO_TIER: dict[str, str] = {
    "P0": "hot",
    "P1": "standard",
    "P2": "enrichment",
}

# ─────────────────────────────────────────────────────────────────────────────
# Input types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CandidateInput:
    """Caller-constructed candidate data for brief assembly."""

    id: int
    campaign_family: str | None = None
    decision_state: str | None = None
    metrics_json: dict[str, Any] | None = None
    deliverables: Any | None = None
    owner_user_id: int | None = None
    # Optional fields mirroring Node shape
    priority: str | None = None  # "P0" | "P1" | "P2"
    why: str | None = None  # verbatim evidence / why flagged
    reviewer: str | None = None
    last_decision_at: str | None = None


@dataclass
class SignalContext:
    """A related signal for the brief's evidence section."""

    reason_codes: list[Any] = field(default_factory=list)
    verbatim_snippet: str | None = None
    urgency_tier: str | None = None
    state: str | None = None
    headline: str | None = None


@dataclass
class QualificationSummary:
    """Summary of qualification result for including in the brief."""

    adjusted_score: float | None = None
    recommended_families: list[dict[str, Any]] = field(default_factory=list)
    qualified_at: str | None = None
    ruleset_versions_used: dict[str, str] = field(default_factory=dict)


@dataclass
class AssetContext:
    """A linked content asset referenced in the brief."""

    asset_id: int
    asset_type: str
    summary: str | None = None
    link_role: str | None = None


@dataclass
class DistrictData:
    """Optional district-level data (unavailable in Lite)."""

    district_id: str | None = None
    district_name: str | None = None


@dataclass
class ContactData:
    """Optional contact data (unavailable in Lite)."""

    contacts: list[dict[str, Any]] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Output type
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CampaignBrief:
    """Assembled campaign brief ready for DB storage."""

    content: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return self.content


# ─────────────────────────────────────────────────────────────────────────────
# Core function
# ─────────────────────────────────────────────────────────────────────────────


def assemble_brief(
    candidate: CandidateInput,
    signals: list[SignalContext] | None = None,
    qualification_summary: QualificationSummary | None = None,
    linked_assets: list[AssetContext] | None = None,
    district_data: DistrictData | None = None,
    contact_data: ContactData | None = None,
) -> CampaignBrief:
    """Assemble an immutable Campaign Brief from candidate + supporting data.

    Pure: no DB, no LLM, no I/O.

    Returns a CampaignBrief whose .content matches the Node brief_json shape.
    district_data_unavailable and contacts_unavailable flags are set when the
    corresponding source data is None (always True in the current Lite build).
    """
    signals = signals or []
    linked_assets = linked_assets or []

    # --- Signal evidence ---
    # Collect reason codes from all related signals
    all_reason_codes: list[str] = []
    for sig in signals:
        for rc in sig.reason_codes or []:
            if isinstance(rc, str) and rc.strip():
                all_reason_codes.append(rc.strip())
            elif isinstance(rc, dict) and rc.get("code"):
                all_reason_codes.append(str(rc["code"]).strip())

    # Verbatim evidence: prefer candidate.why, fall back to first signal snippet
    verbatim_evidence: str | None = None
    if candidate.why and str(candidate.why).strip():
        verbatim_evidence = str(candidate.why).strip()
    elif signals and signals[0].verbatim_snippet:
        verbatim_evidence = str(signals[0].verbatim_snippet).strip()

    # Urgency: from first signal, or candidate priority
    urgency_tier: str | None = None
    if signals and signals[0].urgency_tier:
        urgency_tier = signals[0].urgency_tier
    elif candidate.priority:
        urgency_tier = _PRIORITY_TO_TIER.get(candidate.priority)

    # Campaign family
    primary_type: str | None = (
        str(candidate.campaign_family).strip()
        if candidate.campaign_family and str(candidate.campaign_family).strip()
        else None
    )

    # Deliverables and gates from candidate
    raw_deliverables = candidate.deliverables
    if isinstance(raw_deliverables, list):
        clean_deliverables = [d for d in raw_deliverables if isinstance(d, str) and d.strip()]
    else:
        clean_deliverables = []

    gates: list[str] = []  # not present in Lite candidate row; extensible later

    # Metrics
    metrics: dict[str, Any] = {}
    if isinstance(candidate.metrics_json, dict):
        metrics = candidate.metrics_json

    # Qualification info
    qual_score: float | None = None
    qual_recommended: list[dict[str, Any]] = []
    qual_at: str | None = None
    ruleset_versions: dict[str, str] = {}
    if qualification_summary is not None:
        qual_score = qualification_summary.adjusted_score
        qual_recommended = qualification_summary.recommended_families
        qual_at = qualification_summary.qualified_at
        ruleset_versions = qualification_summary.ruleset_versions_used

    # Linked assets
    asset_refs = [
        {
            "assetId": a.asset_id,
            "assetType": a.asset_type,
            "summary": a.summary,
            "linkRole": a.link_role,
        }
        for a in linked_assets
    ]

    # District availability
    district_data_unavailable = district_data is None
    district_payload: dict[str, Any] | None = None
    if district_data is not None:
        district_payload = {
            "districtId": district_data.district_id,
            "districtName": district_data.district_name,
        }

    # Contact availability
    contacts_unavailable = contact_data is None
    target_contacts: list[dict[str, Any]] = []
    if contact_data is not None:
        target_contacts = contact_data.contacts

    content: dict[str, Any] = {
        # Identity + provenance
        "campaignId": candidate.id,
        "assembledAt": datetime.now(UTC).isoformat(),
        "source": "campaign_candidate",
        "sourceCandidateId": candidate.id,
        "candidateDecisionState": candidate.decision_state,
        # District
        "district": district_payload,
        "districtDataUnavailable": district_data_unavailable,
        # Signal evidence
        "signal": {
            "reasonCodesWithEvidence": all_reason_codes,
            "verbatimEvidence": verbatim_evidence,
            "sourceAttribution": None,
            "urgency": {
                "tier": urgency_tier,
                "deadline": None,
            },
        },
        # Campaign type
        "campaignType": {
            "primary": primary_type,
            "secondary": [],
        },
        # Contacts
        "targetContacts": target_contacts,
        "contactsUnavailable": contacts_unavailable,
        # Audience tier
        "audienceTierDistribution": None,
        "audienceTierUnavailable": True,
        # Deliverables and gates
        "deliverables": clean_deliverables,
        "gates": gates,
        # Metrics
        "metrics": metrics,
        # Decision summary
        "decisionSummary": {
            "state": candidate.decision_state,
            "reviewer": candidate.reviewer,
            "lastDecisionAt": candidate.last_decision_at,
        },
        # Qualification summary (Python extension vs. Node Lite)
        "qualificationSummary": {
            "adjustedScore": qual_score,
            "recommendedFamilies": qual_recommended,
            "qualifiedAt": qual_at,
            "rulesetVersionsUsed": ruleset_versions,
        },
        # Linked assets (Python extension vs. Node Lite)
        "linkedAssets": asset_refs,
    }

    return CampaignBrief(content=content)


def format_brief_for_writing_studio(brief: CampaignBrief) -> str:
    """Format an assembled brief as a readable text block for Writing Studio.

    Port of Node's formatBriefForWritingStudio(). Sections omitted when
    null/empty. Never emits "null" or "undefined" as literal text.
    """
    data = brief.content
    lines: list[str] = []

    signal = data.get("signal") or {}
    if signal.get("verbatimEvidence"):
        lines.append(f"Signal / Why: {signal['verbatimEvidence']}")

    campaign_type = data.get("campaignType") or {}
    if campaign_type.get("primary"):
        lines.append(f"Campaign type: {campaign_type['primary']}")

    reason_codes = [rc for rc in (signal.get("reasonCodesWithEvidence") or []) if rc]
    if reason_codes:
        lines.append(f"Signals: {'; '.join(reason_codes)}")

    deliverables = [d for d in (data.get("deliverables") or []) if d]
    if deliverables:
        lines.append(f"Deliverables: {', '.join(deliverables)}")

    gates = [g for g in (data.get("gates") or []) if g]
    if gates:
        lines.append(f"Gates: {', '.join(gates)}")

    urgency = signal.get("urgency") or {}
    if urgency.get("tier"):
        lines.append(f"Urgency: {urgency['tier']}")

    if data.get("districtDataUnavailable"):
        lines.append("[District data not available — no external data source connected]")
    if data.get("contactsUnavailable"):
        lines.append("[Target contacts not available — Contact DB not configured]")
    if data.get("audienceTierUnavailable"):
        lines.append("[Audience tier distribution not available]")

    return "\n".join(lines)
