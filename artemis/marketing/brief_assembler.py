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

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from artemis.agent.client import CompletionRequest
from artemis.agent.types import Message, TextBlock
from artemis.costs.events import record_cost_event
from artemis.marketing.initiation_schemas import CampaignInitiationProposal

logger = logging.getLogger(__name__)

_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "marketing-ops-v1"
    / "agents"
    / "content"
    / "5.1-campaign-brief-assembler.md"
)
_PROPOSAL_MODEL = "claude-haiku-4-5"
_JSON_RE = re.compile(r"\{[\s\S]*\}", re.DOTALL)

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


@dataclass(slots=True)
class InitiationProposalResult:
    candidate_id: int
    proposal: CampaignInitiationProposal | None
    prompt: str
    context: dict[str, Any]
    retries_used: int
    raw_output: str | None = None


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


def _load_brief_assembler_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


def _resolve_district_key(primary_signal: Any) -> str | None:
    """Derive the Argus drawer key from a primary signal.

    Convention (mirrors argus_tools.py dispatch_research schema):
      1. Use signal.district_id (the raw string identifier, e.g. "TX-001") when
         present — this is what Callie passes to dispatch_research so drawer entries
         written by Argus already use it as the key.
      2. Fall back to str(resolved_district_id) when the signal has a resolved DB
         row but no string district_id — rare, but safe to support.
      3. Return None if neither is available (no resolved district → no drawer to read).

    Returns None rather than raising so callers can skip drawer reads gracefully.
    """
    if primary_signal is None:
        return None
    if primary_signal.district_id and str(primary_signal.district_id).strip():
        return str(primary_signal.district_id).strip()
    if primary_signal.resolved_district_id is not None:
        return str(primary_signal.resolved_district_id)
    return None


async def _read_argus_context(
    session: Any,
    district_key: str | None,
) -> dict[str, Any] | None:
    """Read Argus findings for a district and return a structured context dict.

    Returns None when:
      - district_key is None (no resolved district on the signal), or
      - the drawer is empty (Argus has not yet researched this district), or
      - the DB read fails (graceful, logs the error).

    When findings exist the returned dict has the shape:
      {
        "district_key": str,
        "attributed_to": "Argus",
        "findings": {<dimension>: {"value": str, "source": str, "researched_at": str}, ...},
        "recommended_angle": str | None,
      }
    """
    if not district_key:
        return None
    try:
        from artemis.argus.drawer import Dimension, read_district_drawer

        findings = await read_district_drawer(session, district_key)
        if not findings:
            return None

        recommended_angle: str | None = None
        findings_payload: dict[str, Any] = {}
        for dim, finding in findings.items():
            if dim == Dimension.RECOMMENDED_ANGLE:
                recommended_angle = finding.value
            else:
                findings_payload[dim] = {
                    "value": finding.value,
                    "source": finding.source,
                    "researched_at": finding.researched_at,
                }

        return {
            "district_key": district_key,
            "attributed_to": "Argus",
            "findings": findings_payload,
            "recommended_angle": recommended_angle,
        }
    except Exception:
        logger.warning(
            "build_campaign_initiation_context: Argus drawer read failed for "
            "district_key=%r (skipping; no research context will be included)",
            district_key,
            exc_info=True,
        )
        return None


async def build_campaign_initiation_context(
    session: Any,
    candidate_id: int,
) -> dict[str, Any]:
    from artemis.marketing.repository import (
        get_candidate,
        get_candidate_predecessor_context,
        get_candidate_primary_signal,
        get_candidate_signal_rows,
        get_district,
        list_deliverable_types,
    )

    candidate = await get_candidate(session, candidate_id)
    signals = await get_candidate_signal_rows(session, candidate_id)
    primary_signal = await get_candidate_primary_signal(session, candidate_id)
    predecessor = await get_candidate_predecessor_context(session, candidate_id)
    active_deliverables = await list_deliverable_types(session, active_only=True)
    active_slugs = [row.slug for row in active_deliverables]

    # Default targeting from the signal's geography, narrowest-first:
    #   resolved district's state → that state; else the signal's OWN state → that state;
    #   else (no geography at all) → all districts.
    # A Florida policy signal must default to FL districts, not all 1903 nationwide — even when
    # the district is unresolved, the signal still carries its state, so use it.
    # Emits the COMPOSITE shape (base key) for new proposals so the new audience builder
    # pre-selects correctly; legacy shape is still read from old rows.
    default_target_scope: dict[str, Any] = {"base": "all"}
    if primary_signal is not None:
        district_state: str | None = None
        if primary_signal.resolved_district_id is not None:
            district = await get_district(session, primary_signal.resolved_district_id)
            district_state = district.state if district is not None else None
        district_state = district_state or primary_signal.state
        if district_state:
            default_target_scope = {"base": "states", "states": [str(district_state).upper()]}

    # Argus district research — read the per-district drawer so any prior research
    # (competitor intel, procurement timing, decision-makers, recommended angle)
    # automatically enriches this campaign brief. Empty drawer → no change (backward-compat).
    district_key = _resolve_district_key(primary_signal)
    argus_research = await _read_argus_context(session, district_key)

    context: dict[str, Any] = {
        "candidate": {
            "id": candidate.id,
            "campaign_family": candidate.campaign_family,
            "decision_state": candidate.decision_state,
            "predecessor_id": candidate.predecessor_id,
        },
        "signals": [
            {
                "id": signal.id,
                "headline": signal.headline,
                "summary": signal.summary,
                "state": signal.state,
                "district_id": signal.district_id,
                "resolved_district_id": signal.resolved_district_id,
                "urgency_tier": signal.urgency_tier,
                "reason_codes": signal.reason_codes or [],
                "source_url": signal.source_url,
            }
            for signal in signals
        ],
        "predecessor": (
            {
                "candidate_id": predecessor.candidate_id,
                "name": predecessor.name,
                "objective": predecessor.objective,
                "latest_brief": predecessor.latest_brief,
                "linked_assets": predecessor.linked_assets,
            }
            if predecessor is not None
            else None
        ),
        "default_target_scope": default_target_scope,
        "active_deliverable_type_slugs": active_slugs,
        "default_recommended_deliverable_types": ["outreach_email"]
        if "outreach_email" in active_slugs
        else active_slugs[:1],
    }
    # Only inject the argus_research key when findings exist so the context dict
    # shape is identical to the pre-Argus shape for districts with no research.
    if argus_research is not None:
        context["argus_research"] = argus_research
    return context


def _build_campaign_initiation_prompt(context: dict[str, Any]) -> str:
    prompt_scaffold = _load_brief_assembler_prompt().strip()
    context_json = json.dumps(context, indent=2, sort_keys=True)

    # When Argus has researched this district, surface a human-readable research
    # block BEFORE the raw JSON so the model sees the key findings prominently.
    argus_section = ""
    argus_research = context.get("argus_research")
    if argus_research:
        lines: list[str] = [
            "## District Research (from Argus)",
            f"District: {argus_research.get('district_key', 'unknown')}",
            "Source: Argus (Callie's dedicated research agent) — use these findings to",
            "inform the campaign angle, positioning, and targeting.",
        ]
        recommended_angle = argus_research.get("recommended_angle")
        if recommended_angle:
            lines.append(f"\nRecommended angle: {recommended_angle}")
        findings = argus_research.get("findings") or {}
        if findings:
            lines.append("\nResearched dimensions:")
            for dim, detail in sorted(findings.items()):
                value = detail.get("value", "")
                researched_at = detail.get("researched_at", "")
                date_str = f" (as of {researched_at})" if researched_at else ""
                lines.append(f"  • {dim}{date_str}: {value}")
        argus_section = "\n".join(lines) + "\n\n"

    return (
        f"{prompt_scaffold}\n\n"
        f"{argus_section}"
        "## Runtime Task\n"
        "Read the full candidate signal cluster and produce exactly one "
        "CampaignInitiationProposal JSON object. Return JSON only.\n\n"
        "## Candidate Context\n"
        f"{context_json}\n"
    )


async def propose_campaign_initiation(
    session: Any,
    candidate_id: int,
    *,
    model_adapter: Any | None = None,
    max_retries: int = 1,
) -> InitiationProposalResult:
    from artemis.marketing.repository import save_initiation_proposal
    from artemis.providers.resolver import resolve_adapter_async

    context = await build_campaign_initiation_context(session, candidate_id)
    prompt = _build_campaign_initiation_prompt(context)
    if model_adapter is not None:
        adapter = model_adapter
    else:
        adapter = await resolve_adapter_async(
            provider="claude-code",
            fallback_provider="anthropic",
            feature_tag="marketing_brief",
            session=session,
        )
    last_error: str | None = None
    raw_output: str | None = None

    for attempt in range(max_retries + 1):
        current_prompt = prompt
        if last_error is not None:
            current_prompt = (
                f"{prompt}\n\n"
                "[CORRECTION NEEDED] Your previous response failed schema validation:\n"
                f"{last_error}\n"
                "Return one valid CampaignInitiationProposal JSON object only."
            )

        response = await adapter.complete(
            CompletionRequest(
                messages=[Message(role="user", content=[TextBlock(text=current_prompt)])],
                system=(
                    "You are a JSON-output assistant. Return ONLY valid JSON matching "
                    "CampaignInitiationProposal. No markdown fences. No prose."
                ),
                max_tokens=1024,
                model=_PROPOSAL_MODEL,
            )
        )
        raw_output = "".join(
            block.text for block in response.message.content if isinstance(block, TextBlock)
        )

        match = _JSON_RE.search(raw_output)
        if not match:
            last_error = f"No JSON object found in response: {raw_output[:200]!r}"
            continue

        try:
            proposal = CampaignInitiationProposal.validate_with_active_slugs(
                json.loads(match.group(0)),
                context["active_deliverable_type_slugs"],
            )
        except (ValidationError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue

        await save_initiation_proposal(session, candidate_id, proposal)
        # Record cost — never propagate failures.
        # Uses adapter_identity to report the resolved adapter's real provider/path
        # (was previously hardcoded anthropic/api regardless of routing). Tags the
        # row with campaign_candidate_id for the per-campaign rollup.
        try:
            from artemis.costs.events import adapter_identity

            _provider, _, _path = adapter_identity(adapter)
            await record_cost_event(
                session,
                provider=_provider,
                model=_PROPOSAL_MODEL,
                provider_path=_path,
                feature_tag="marketing_brief",
                input_tokens=getattr(response.usage, "input_tokens", 0),
                output_tokens=getattr(response.usage, "output_tokens", 0),
                cache_creation_input_tokens=getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ),
                cache_read_input_tokens=getattr(response.usage, "cache_read_input_tokens", 0),
                source_kind="agent_run",
                source_id=str(candidate_id),
                campaign_candidate_id=candidate_id,
            )
        except Exception:
            logger.warning(
                "cost_event recording failed in brief_assembler candidate_id=%s",
                candidate_id,
                exc_info=True,
            )
        return InitiationProposalResult(
            candidate_id=candidate_id,
            proposal=proposal,
            prompt=current_prompt,
            context=context,
            retries_used=attempt,
            raw_output=raw_output,
        )

    logger.warning(
        "Campaign initiation proposal validation failed for candidate %s: %s",
        candidate_id,
        last_error,
    )
    return InitiationProposalResult(
        candidate_id=candidate_id,
        proposal=None,
        prompt=prompt,
        context=context,
        retries_used=max_retries,
        raw_output=raw_output,
    )
