"""Campaign initiation routes — /api/marketing/campaigns."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.initiation_schemas import CampaignInitiationProposal, TargetScope
from artemis.marketing.models import CampaignCandidate, SignalQueue
from artemis.marketing.repository import (
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

router = APIRouter(
    prefix="/api/marketing/campaigns",
    tags=["marketing-campaigns"],
    dependencies=[Depends(require_token)],
)

logger = logging.getLogger(__name__)


class InitiateCampaignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    objective: str = Field(min_length=1, max_length=500)
    owner_user_id: int | None = None
    deliverable_type_slugs: list[str] = Field(default_factory=list, min_length=1)
    target_scope: TargetScope
    actor: str | None = None


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
    lineage = await get_candidate_lineage_context(session, candidate_id)
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
        "lineage": [_serialize_lineage_row(item) for item in lineage],
        "pipelineRunId": pipeline_run_id,
        "pipelineRunRole": "discovery",
        "gateNodeId": None,
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
        return {
            "resolved": False,
            "label": "All districts",
            "note": "District unresolved — targeting defaults to All districts.",
            "districtId": None,
            "name": None,
            "state": None,
            "tier": None,
            "supported": None,
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
            "supported": None,
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
        "supported": district.supported,
        "defaultTargetScope": default_scope,
    }


def _resolve_pipeline_run_id(signal_cluster: list[dict[str, Any]]) -> str | None:
    run_ids = [row.get("pipelineRunId") for row in signal_cluster if row.get("pipelineRunId")]
    unique = list(dict.fromkeys(str(run_id) for run_id in run_ids if run_id is not None))
    return unique[0] if unique else None


def _serialize_signal_row(
    signal: SignalQueue, primary_signal: SignalQueue | None
) -> dict[str, Any]:
    district_id = signal.resolved_district_id
    return {
        "signalId": signal.id,
        "headline": signal.headline,
        "summary": signal.summary,
        "campaignFamily": signal.campaign_family,
        "state": signal.state,
        "resolvedDistrictId": district_id,
        "pipelineRunId": signal.pipeline_run_id,
        "reasonCodes": signal.reason_codes or [],
        "isPrimary": primary_signal is not None and signal.id == primary_signal.id,
    }


def _serialize_lineage_row(item: Any) -> dict[str, Any]:
    return {
        "candidateId": item.candidate_id,
        "name": item.name,
        "objective": item.objective,
        "latestBrief": item.latest_brief,
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
