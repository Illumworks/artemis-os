"""Campaign initiation routes — /api/marketing/campaigns."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.initiation_schemas import CampaignInitiationProposal, TargetScope
from artemis.marketing.models import CampaignCandidate, District, SignalQueue
from artemis.marketing.repository import (
    get_campaign_brief,
    get_candidate,
    get_candidate_lineage_context,
    get_candidate_primary_signal,
    get_candidate_signal_rows,
    get_district,
    initiate_campaign,
    list_candidates,
    list_deliverable_types,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found, validation_failed
from artemis.pipelines import repository as pipeline_repo
from artemis.pipelines.routes import _dispatch_execution
from artemis.pipelines.seeds.marketing_pipeline import CAMPAIGN_DELIVERABLES_PIPELINE_ID

# Decision-history categories surfaced in the trendContext enrichment
_DECISION_HISTORY_CATEGORIES = frozenset({"signal_gate1_decision", "pipeline_gate_decision"})

router = APIRouter(
    prefix="/api/marketing/campaigns",
    tags=["marketing-campaigns"],
    dependencies=[Depends(require_token)],
)

logger = logging.getLogger(__name__)

_EXPLICIT_REJECT_RE = re.compile(r"\brejected\s+(?:signal|pipeline|gate)\b|\bdeclined\b")
_EXPLICIT_APPROVE_RE = re.compile(r"\bapproved\s+(?:signal|pipeline|gate)\b")
_REJECT_WORD_RE = re.compile(r"\breject(?:ed|ion)?\b")
_APPROVE_WORD_RE = re.compile(r"\bapprove(?:d)?\b")
_REJECT_FALSE_POSITIVE_RE = re.compile(r"\b(?:not rejected|no rejection)\b")


def _classify_decision_history_content(content: str) -> str | None:
    """Classify a decision-history observation as approved/rejected/unknown.

    Prefer the fixed MC2/MC4 content templates first, then fall back to
    word-boundary matching while excluding known false-positive phrases such as
    "not rejected" and "no rejection".
    """
    content_lower = content.lower()
    if _EXPLICIT_REJECT_RE.search(content_lower):
        return "rejected"
    if _EXPLICIT_APPROVE_RE.search(content_lower):
        return "approved"
    if _REJECT_FALSE_POSITIVE_RE.search(content_lower):
        return None
    if _REJECT_WORD_RE.search(content_lower):
        return "rejected"
    if _APPROVE_WORD_RE.search(content_lower):
        return "approved"
    return None


class InitiateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    owner_user_id: int | None = None
    deliverable_type_slugs: list[str] = Field(default_factory=list, min_length=1)
    target_scope: TargetScope
    actor: str | None = None
    skip_list_acknowledged: bool = False


@router.get("")
@router.get("/")
async def list_campaigns(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the real campaign candidate list for the marketing surface."""
    candidates = await list_candidates(session, limit=200)
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        signal_rows = await get_candidate_signal_rows(session, candidate.id)
        primary_signal = signal_rows[0] if signal_rows else None
        proposal = (
            candidate.initiation_proposal_json
            if isinstance(candidate.initiation_proposal_json, dict)
            else {}
        )
        items.append(
            {
                "id": candidate.id,
                "name": candidate.name or proposal.get("name") or "",
                "objective": candidate.objective or proposal.get("objective") or "",
                "state": candidate.decision_state,
                "family": candidate.campaign_family,
                "initiatedAt": candidate.initiated_at.isoformat()
                if candidate.initiated_at
                else None,
                "signalClusterCount": len(signal_rows),
                "clusterCount": len(signal_rows),
                "primarySignalId": primary_signal.id if primary_signal is not None else None,
                "primarySignalState": primary_signal.state if primary_signal is not None else None,
                "primarySignalUrgencyTier": primary_signal.urgency_tier
                if primary_signal is not None
                else None,
                "primarySignalHeadline": primary_signal.headline
                if primary_signal is not None
                else None,
            }
        )
    return {"campaigns": items, "total": len(items)}


@router.get("/{candidate_id}/initiation-context")
@router.get("/{candidate_id}/initiation-proposal")
async def get_initiation_proposal(
    candidate_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        candidate = await get_candidate(session, candidate_id)
    except ValueError as exc:
        raise not_found("Campaign candidate not found", "campaign_candidate_not_found") from exc

    proposal_json = await _load_or_generate_proposal(session, candidate)

    active_deliverables = await list_deliverable_types(session, active_only=False)
    active_slugs = [row.slug for row in active_deliverables if row.active]
    try:
        proposal = CampaignInitiationProposal.validate_with_active_slugs(
            proposal_json,
            active_slugs,
        )
    except ValidationError as exc:
        raise validation_failed({"errors": exc.errors()}) from exc

    primary_signal = await get_candidate_primary_signal(session, candidate_id)
    signals = await get_candidate_signal_rows(session, candidate_id)
    signal_cluster = [_serialize_signal_row(signal, primary_signal) for signal in signals]

    district_context = await _build_district_context(session, primary_signal)
    trend_context = await _build_trend_context(session, primary_signal)
    lineage = await get_candidate_lineage_context(session, candidate_id)
    proposal_scope = proposal.target_scope.model_dump(mode="json")
    # Targeting geography is deterministic from the signal, not an LLM choice. If the generated
    # proposal left target_scope at all_districts but the signal's geography implies a narrower
    # default (e.g. a FL signal → FL districts), pre-select that narrower default instead of all
    # 1903 nationwide. The user can still widen it; on confirm, the submitted scope wins.
    _default_scope = district_context.get("defaultTargetScope") or {"mode": "all_districts"}
    if proposal_scope.get("mode") == "all_districts" and _default_scope.get("mode") != "all_districts":
        proposal.target_scope = TargetScope.model_validate(_default_scope)
        proposal_scope = proposal.target_scope.model_dump(mode="json")
    pipeline_run_id = _resolve_pipeline_run_id(signal_cluster)

    return {
        "candidateId": candidate.id,
        "campaignFamily": candidate.campaign_family,
        "initiatedAt": candidate.initiated_at.isoformat() if candidate.initiated_at else None,
        "initiatedBy": candidate.initiated_by,
        "proposal": proposal.model_dump(mode="json"),
        "signalCluster": signal_cluster,
        "deliverableRegistry": [
            {
                "slug": row.slug,
                "label": row.label,
                "defaultEnabled": row.default_enabled,
                "active": row.active,
                "displayOrder": row.display_order,
            }
            for row in active_deliverables
        ],
        "districtContext": district_context,
        "defaultTargetScope": district_context["defaultTargetScope"],
        "metricsJson": candidate.metrics_json if isinstance(candidate.metrics_json, dict) else {},
        "targetScopeCounts": await _build_target_scope_counts(session),
        "selectedTargetScopeCount": await _count_districts_for_scope(session, proposal_scope),
        "lineage": [_serialize_lineage_row(item) for item in lineage],
        "pipelineRunId": pipeline_run_id,
        "pipelineRunRole": "discovery",
        "gateNodeId": None,
        "trendContext": trend_context,
    }


@router.post("/{candidate_id}/initiate")
async def initiate(
    candidate_id: int,
    body: InitiateCampaignRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        candidate = await get_candidate(session, candidate_id)
    except ValueError as exc:
        raise not_found("Campaign candidate not found", "campaign_candidate_not_found") from exc

    primary_signal = await get_candidate_primary_signal(session, candidate_id)
    district_context = await _build_district_context(session, primary_signal)
    if district_context.get("onSkipList") is True and not body.skip_list_acknowledged:
        raise validation_failed(
            {
                "errors": [
                    {
                        "loc": ["body", "skip_list_acknowledged"],
                        "msg": (
                            "skip_list_acknowledged must be true before initiating a "
                            "skip-listed district campaign"
                        ),
                        "type": "value_error",
                    }
                ]
            }
        )

    proposal_json = candidate.initiation_proposal_json
    if not isinstance(proposal_json, dict):
        raise conflict(
            "Campaign initiation proposal is not available yet",
            "initiation_proposal_missing",
        )

    active_deliverables = await list_deliverable_types(session, active_only=True)
    active_slugs = {row.slug for row in active_deliverables}
    invalid_slugs = [slug for slug in body.deliverable_type_slugs if slug not in active_slugs]
    if invalid_slugs:
        raise validation_failed(
            {
                "errors": [
                    {
                        "loc": ["body", "deliverable_type_slugs"],
                        "msg": (
                            "deliverable_type_slugs must be active deliverable type slugs. "
                            f"Invalid: {', '.join(invalid_slugs)}. "
                            f"Active: {', '.join(sorted(active_slugs))}"
                        ),
                        "type": "value_error",
                    }
                ]
            }
        )

    try:
        await pipeline_repo.get_pipeline(session, CAMPAIGN_DELIVERABLES_PIPELINE_ID)
    except ValueError as exc:
        raise conflict(
            "Campaign deliverables pipeline is not seeded",
            "campaign_deliverables_pipeline_missing",
        ) from exc

    if await get_campaign_brief(session, candidate_id) is None:
        raise conflict(
            "Campaign brief is required before dispatching a deliverables run",
            "campaign_brief_missing",
        )

    try:
        initiated = await initiate_campaign(
            session,
            candidate_id,
            name=body.name,
            objective=body.objective,
            owner_user_id=body.owner_user_id,
            target_scope=body.target_scope,
            deliverable_type_slugs=body.deliverable_type_slugs,
            initiated_by=body.owner_user_id,
        )
        await session.commit()
    except ValueError as exc:
        message = str(exc)
        if "already initiated" in message:
            raise conflict(message, "campaign_already_initiated") from exc
        if "is rejected and cannot be initiated" in message:
            raise conflict(message, "campaign_rejected") from exc
        raise bad_request(message, "campaign_initiation_invalid") from exc

    new_run = await pipeline_repo.create_pipeline_run(
        session,
        pipeline_id=CAMPAIGN_DELIVERABLES_PIPELINE_ID,
        status="queued",
        trigger="manual",
        triggered_by=body.actor
        or (
            f"user:{body.owner_user_id}"
            if body.owner_user_id is not None
            else "campaign_initiation_ui"
        ),
        target_candidate_id=candidate_id,
        metadata_={
            "source": "campaign_initiation",
            "target_candidate_id": candidate_id,
            "deliverable_type_slugs": body.deliverable_type_slugs,
        },
    )
    await session.commit()

    dispatch_error: str | None = None
    try:
        _dispatch_execution(new_run.id)
    except Exception as exc:  # noqa: BLE001
        dispatch_error = str(exc)
        logger.warning(
            "campaign initiation dispatch failed for candidate %s run %s: %s",
            candidate_id,
            new_run.id,
            exc,
        )

    await session.refresh(initiated)
    payload = _serialize_candidate(initiated)
    payload["deliverableRunId"] = new_run.id
    if dispatch_error is not None:
        payload["dispatchError"] = dispatch_error
    return payload


async def _build_district_context(
    session: AsyncSession,
    primary_signal: SignalQueue | None,
) -> dict[str, Any]:
    if primary_signal is None or primary_signal.resolved_district_id is None:
        # District unresolved — but the signal still carries its state. Default targeting to
        # that state's districts (a FL signal → FL districts), not all 1903 nationwide. Only
        # fall back to All districts when there's no geography at all.
        signal_state = (
            (primary_signal.state or "").upper() or None if primary_signal is not None else None
        )
        if signal_state:
            return {
                "resolved": False,
                "label": f"All {signal_state} districts",
                "note": (
                    f"District unresolved — targeting defaults to all {signal_state} districts "
                    "(from the signal's state)."
                ),
                "districtId": None,
                "name": None,
                "state": signal_state,
                "tier": None,
                "enrollment": None,
                "supported": None,
                "onSkipList": None,
                "defaultTargetScope": {"mode": "states", "states": [signal_state]},
            }
        return {
            "resolved": False,
            "label": "All districts",
            "note": "District unresolved and no signal state — targeting defaults to All districts.",
            "districtId": None,
            "name": None,
            "state": None,
            "tier": None,
            "enrollment": None,
            "supported": None,
            "onSkipList": None,
            "defaultTargetScope": {"mode": "all_districts"},
        }

    district = await get_district(session, primary_signal.resolved_district_id)
    if district is None:
        state = (primary_signal.state or "").upper() or None
        return {
            "resolved": False,
            "label": "All districts",
            "note": "District could not be resolved — targeting defaults to All districts.",
            "districtId": primary_signal.resolved_district_id,
            "name": None,
            "state": state,
            "tier": None,
            "enrollment": None,
            "supported": None,
            "onSkipList": None,
            "defaultTargetScope": {"mode": "all_districts"},
        }

    default_scope: dict[str, Any] = {"mode": "all_districts"}
    if district.state:
        default_scope = {"mode": "states", "states": [district.state]}
    return {
        "resolved": True,
        "label": f"{district.name} ({district.tier or 'unclassified'})",
        "note": None,
        "districtId": district.id,
        "name": district.name,
        "state": district.state,
        "tier": district.tier,
        "enrollment": district.enrollment,
        "supported": district.supported,
        "onSkipList": district.on_skip_list,
        "defaultTargetScope": default_scope,
    }


async def _fetch_decision_history(
    session: AsyncSession,
    *,
    theme: str,
    region: str | None,
    limit: int = 25,
    top_matches: int = 5,
) -> dict[str, Any]:
    """Read Gate-1 and pipeline gate decisions from the memory keystone for this theme+region.

    Queries via search_observations with scope=(workspace, marketing), filtered to
    categories signal_gate1_decision and pipeline_gate_decision.
    Returns counts (priorApproves, priorRejects) and top N observation snippets.

    Query strategy: theme + region combined (e.g. "obc TX") so FTS + semantic
    retrieval biases toward the most relevant past decisions first.  When region is
    None, uses theme alone; category filtering is applied post-retrieval in Python
    because search_observations does not accept a category allowlist parameter.
    """
    from artemis.memory.retrieval import search_observations
    from artemis.memory.schemas import Scope

    scope_set = [Scope(scope_kind="workspace", scope_id="marketing")]
    query_terms = theme if region is None else f"{theme} {region}"

    try:
        results = await search_observations(
            session,
            scope_set=scope_set,
            query=query_terms,
            limit=limit,
        )
    except Exception:
        logger.warning("decision_history search failed", exc_info=True)
        return {"priorApproves": 0, "priorRejects": 0, "topMatches": []}

    # Filter to the two target categories
    filtered = [r for r in results if r.category in _DECISION_HISTORY_CATEGORIES]

    classified: list[tuple[Any, str]] = []
    prior_approves = 0
    prior_rejects = 0
    for obs in filtered:
        decision_label = _classify_decision_history_content(obs.content)
        if decision_label is None:
            continue
        classified.append((obs, decision_label))
        if decision_label == "rejected":
            prior_rejects += 1
        else:
            prior_approves += 1

    top: list[dict[str, Any]] = []
    for obs, decision_label in classified[:top_matches]:
        # Trim content to a short snippet (first 200 chars)
        summary = obs.content[:200].strip()
        if len(obs.content) > 200:
            summary += "…"
        top.append(
            {
                "observationId": obs.id,
                "category": obs.category,
                "decision": decision_label,
                "summary": summary,
                "createdAt": obs.created_at.isoformat(),
            }
        )

    return {
        "priorApproves": prior_approves,
        "priorRejects": prior_rejects,
        "topMatches": top,
    }


async def _build_trend_context(
    session: AsyncSession,
    primary_signal: SignalQueue | None,
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Build the trendContext enrichment block for the initiation-proposal response.

    Computes momentum (weekly signal time-series), comparable districts count,
    and decision history from the memory keystone — all deterministic, no LLM.

    Returns a minimal block with resolved=False when the primary signal is missing
    or has no campaign_family / state, so the rest of the proposal is unaffected.
    """
    from artemis.marketing.intel.trends import compute_momentum, count_comparable_districts

    if primary_signal is None or not primary_signal.campaign_family:
        return {"resolved": False, "reason": "no_primary_signal"}

    theme: str = primary_signal.campaign_family
    region: str | None = primary_signal.state or None
    _as_of = as_of or datetime.now(UTC)

    try:
        momentum = await compute_momentum(
            session,
            theme=theme,
            region=region,
            as_of=_as_of,
            window_days=90,
            bucket_days=7,
        )
        comparables = await count_comparable_districts(
            session,
            theme=theme,
            region=region,
            as_of=_as_of,
            window_days=90,
        )
        decision_history = await _fetch_decision_history(
            session,
            theme=theme,
            region=region,
        )
    except Exception:
        logger.warning("trend_context computation failed", exc_info=True)
        return {"resolved": False, "reason": "computation_error"}

    from artemis.marketing.intel.schemas import TrendSnapshot
    from artemis.marketing.intel.trends import persist_trend_snapshot

    snapshot = TrendSnapshot(
        as_of=_as_of,
        theme=theme,
        region=region,
        snapshot_kind="momentum",
        content_summary=(
            f"Momentum snapshot for {theme}/{region or 'all'} at {_as_of.isoformat()}: "
            f"current={momentum.current_window_count}, prior={momentum.prior_window_count}, "
            f"delta_ratio={momentum.delta_ratio}"
        ),
        payload={
            "momentum": momentum.model_dump(mode="json"),
            "comparables": comparables.model_dump(mode="json"),
        },
    )
    await persist_trend_snapshot(
        session,
        snapshot=snapshot,
        primary_scope_kind="workspace",
        primary_scope_id="marketing",
        additional_scopes=(
            [("state", region), ("campaign_family", theme)]
            if region is not None
            else [("campaign_family", theme)]
        ),
    )

    return {
        "resolved": True,
        "asOf": _as_of.isoformat(),
        "theme": theme,
        "region": region,
        "momentum": momentum.model_dump(mode="json"),
        "comparables": comparables.model_dump(mode="json"),
        "decisionHistory": decision_history,
    }


def _resolve_pipeline_run_id(signal_cluster: list[dict[str, Any]]) -> str | None:
    run_ids = [row.get("pipelineRunId") for row in signal_cluster if row.get("pipelineRunId")]
    unique = list(dict.fromkeys(str(run_id) for run_id in run_ids if run_id is not None))
    return unique[0] if unique else None


def _serialize_signal_row(
    signal: SignalQueue, primary_signal: SignalQueue | None
) -> dict[str, Any]:
    district_id = signal.resolved_district_id
    provenance = signal.provenance if isinstance(signal.provenance, dict) else {}
    return {
        "signalId": signal.id,
        "headline": signal.headline,
        "summary": signal.summary,
        "campaignFamily": signal.campaign_family,
        "state": signal.state,
        "resolvedDistrictId": district_id,
        "pipelineRunId": signal.pipeline_run_id,
        "reasonCodes": signal.reason_codes or [],
        "whyFlagged": provenance.get("why_flagged")
        or provenance.get("whyFlagged")
        or signal.summary,
        "sourceUrl": signal.source_url,
        "sourceTitle": provenance.get("source_title")
        or provenance.get("sourceTitle")
        or signal.source_url,
        "sourcePublishedAt": provenance.get("source_published_at")
        or provenance.get("sourcePublishedAt"),
        "sourceAuthor": provenance.get("source_author")
        or provenance.get("sourceAuthor")
        or provenance.get("speakerAttribution"),
        "discoveredBy": signal.discovered_by,
        "agentRunId": provenance.get("agent_run_id") or provenance.get("agentRunId"),
        "provenance": provenance,
        "qualificationJson": signal.qualification_json,
        "isPrimary": primary_signal is not None and signal.id == primary_signal.id,
    }


def _serialize_lineage_row(item: Any) -> dict[str, Any]:
    return {
        "candidateId": item.candidate_id,
        "name": item.name,
        "objective": item.objective,
        "latestBrief": item.latest_brief,
        "latestBriefSummary": _summarize_latest_brief(item.latest_brief),
        "linkedAssets": item.linked_assets,
        "drafts": item.drafts,
        "actions": {
            "view": True,
            "clone": True,
            "adapt": True,
        },
    }


def _serialize_candidate(candidate: CampaignCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "campaignFamily": candidate.campaign_family,
        "sourceSignalId": candidate.source_signal_id,
        "name": candidate.name,
        "objective": candidate.objective,
        "ownerUserId": candidate.owner_user_id,
        "initiatedAt": candidate.initiated_at.isoformat() if candidate.initiated_at else None,
        "initiatedBy": candidate.initiated_by,
        "targetScopeJson": candidate.target_scope_json,
        "deliverableTypesJson": candidate.deliverable_types_json,
        "predecessorId": candidate.predecessor_id,
        "initiationProposalJson": candidate.initiation_proposal_json,
        "stage": candidate.stage,
        "decisionState": candidate.decision_state,
        "workspaceState": candidate.workspace_state,
        "createdAt": candidate.created_at.isoformat(),
        "updatedAt": candidate.updated_at.isoformat(),
    }


async def _load_or_generate_proposal(
    session: AsyncSession,
    candidate: CampaignCandidate,
) -> dict[str, Any]:
    proposal_json = candidate.initiation_proposal_json
    if isinstance(proposal_json, dict):
        return proposal_json

    from artemis.marketing.brief_assembler import propose_campaign_initiation
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    try:
        result = await propose_campaign_initiation(
            session,
            candidate.id,
            model_adapter=resolve_adapter("claude-code", "anthropic"),
        )
    except NoProviderAvailableError as exc:
        raise bad_request(
            "Campaign initiation proposal could not be generated: no LLM provider is available",
            "initiation_proposal_generation_failed",
        ) from exc
    except Exception as exc:
        raise bad_request(
            f"Campaign initiation proposal could not be generated: {exc}",
            "initiation_proposal_generation_failed",
        ) from exc

    if result.proposal is None:
        raise bad_request(
            "Campaign initiation proposal could not be generated: validation failed",
            "initiation_proposal_generation_failed",
        )

    await session.commit()
    await session.refresh(candidate)
    generated = candidate.initiation_proposal_json
    if not isinstance(generated, dict):
        generated = result.proposal.model_dump(mode="json")
    return generated


async def _build_target_scope_counts(session: AsyncSession) -> dict[str, Any]:
    all_districts = await session.scalar(
        select(func.count(District.id)).where(District.supported.is_(True))
    )
    by_state_rows = await session.execute(
        select(District.state, func.count(District.id))
        .where(District.supported.is_(True))
        .group_by(District.state)
    )
    by_tier_rows = await session.execute(
        select(District.tier, func.count(District.id))
        .where(District.supported.is_(True))
        .group_by(District.tier)
    )
    return {
        "allDistricts": int(all_districts or 0),
        "byState": {
            str(state).upper(): int(count)
            for state, count in by_state_rows.all()
            if state is not None
        },
        "byTier": {
            str(tier).upper(): int(count) for tier, count in by_tier_rows.all() if tier is not None
        },
    }


async def _count_districts_for_scope(
    session: AsyncSession,
    target_scope: TargetScope | dict[str, Any] | None,
) -> int:
    if isinstance(target_scope, TargetScope):
        scope = target_scope.model_dump(mode="json")
    elif isinstance(target_scope, dict):
        scope = target_scope
    else:
        scope = {}

    mode = str(scope.get("mode") or "all_districts")
    if mode == "all_districts":
        value = await session.scalar(
            select(func.count(District.id)).where(District.supported.is_(True))
        )
        return int(value or 0)
    if mode == "states":
        states = [str(state).upper() for state in scope.get("states") or [] if state]
        if not states:
            return 0
        value = await session.scalar(
            select(func.count(District.id)).where(
                District.supported.is_(True),
                District.state.in_(states),
            )
        )
        return int(value or 0)
    if mode == "district_tier":
        tiers = [str(tier).upper() for tier in scope.get("tiers") or [] if tier]
        if not tiers:
            return 0
        value = await session.scalar(
            select(func.count(District.id)).where(
                District.supported.is_(True),
                District.tier.in_(tiers),
            )
        )
        return int(value or 0)
    if mode == "named_districts":
        district_ids = [
            district_id for district_id in scope.get("district_ids") or [] if district_id
        ]
        if not district_ids:
            return 0
        value = await session.scalar(
            select(func.count(District.id)).where(District.id.in_(district_ids))
        )
        return int(value or 0)
    return 0


def _summarize_latest_brief(latest_brief: dict[str, Any] | None) -> str | None:
    if not isinstance(latest_brief, dict):
        return None
    for key in ("summary", "briefSummary", "executiveSummary"):
        value = latest_brief.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    signal = latest_brief.get("signal")
    if isinstance(signal, dict):
        verbatim = signal.get("verbatimEvidence")
        if isinstance(verbatim, str) and verbatim.strip():
            return verbatim.strip()
    return None
