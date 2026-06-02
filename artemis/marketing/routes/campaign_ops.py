"""Campaign Ops router — /api/campaign-ops.

Endpoints:
  GET  /candidates               — list candidates (filtered, paginated)
  GET  /candidates/{id}          — get single candidate
  POST /candidates/{id}/brief/assemble — assemble brief (stub until C3)
  POST /candidates/{id}/advance  — advance candidate stage/decision

Note: The Node app had /overview, /writing-handoff, /decision, /promote, /reopen.
The C2 spec asks for /candidates, /candidates/:id, /brief/assemble (stub), /advance.
Assemble is stubbed; advance maps to the Node's /decision endpoint pattern.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.brief_assembler import (
    AssetContext,
    CandidateInput,
    QualificationSummary,
    SignalContext,
    assemble_brief,
)
from artemis.marketing.models import CampaignCandidate, ContentAsset, ContentAssetLink, SignalQueue
from artemis.marketing.repository import (
    create_campaign_brief,
    get_candidate,
    list_candidates,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found
from artemis.marketing.state_machine import BriefState, transition

router = APIRouter(
    prefix="/api/campaign-ops",
    tags=["campaign-ops"],
    dependencies=[Depends(require_token)],
)

_VALID_ACTIONS = {"approve", "reject", "monitor", "request_changes"}

# Decision state transitions (mirrors Node's applyCampaignCandidateAction)
_ACTION_STATE_MAP: dict[str, BriefState] = {
    "approve": BriefState.approved,
    "reject": BriefState.rejected,
    "monitor": BriefState.monitoring,
    "request_changes": BriefState.changes_requested,
}


@router.get("/candidates")
async def list_candidates_route(
    decision_state: str | None = Query(default=None, alias="decisionState"),
    campaign_family: str | None = Query(default=None, alias="campaignFamily"),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List campaign candidates with optional filters."""
    candidates = await list_candidates(
        session,
        decision_state=decision_state,
        campaign_family=campaign_family,
        limit=limit,
        cursor=cursor,
    )
    return {
        "candidates": [_serialize_candidate(c) for c in candidates],
        "total": len(candidates),
    }


@router.get("/candidates/{candidate_id}")
async def get_candidate_route(
    candidate_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single campaign candidate."""
    try:
        candidate = await get_candidate(session, candidate_id)
    except ValueError:
        raise not_found("Campaign candidate not found", "campaign_ops_candidate_not_found")  # noqa: B904
    return _serialize_candidate(candidate)


@router.post("/candidates/{candidate_id}/brief/assemble", status_code=201)
async def assemble_brief_route(
    candidate_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Assemble a Campaign Brief for a candidate (C3 real implementation).

    Loads candidate + signals + qualification summary + linked content assets,
    calls assemble_brief(), and stores the result as a new campaign_briefs row.
    Returns { brief } with the assembled content.
    """
    try:
        candidate = await get_candidate(session, candidate_id)
    except ValueError:
        raise not_found("Campaign candidate not found", "campaign_ops_candidate_not_found")  # noqa: B904

    # Load related signal (if any)
    signals: list[SignalContext] = []
    qual_summary: QualificationSummary | None = None
    if candidate.source_signal_id is not None:
        sig_row = await session.get(SignalQueue, candidate.source_signal_id)
        if sig_row is not None:
            signals = [
                SignalContext(
                    reason_codes=sig_row.reason_codes or [],
                    verbatim_snippet=sig_row.summary or None,
                    urgency_tier=sig_row.urgency_tier,
                    state=sig_row.state,
                    headline=sig_row.headline,
                )
            ]
            # Extract qualification summary from signal's qualification_json
            qual_json = sig_row.qualification_json
            if qual_json and isinstance(qual_json, dict):
                family_scores = qual_json.get("scores", [])
                primary_score = next(
                    (
                        s
                        for s in family_scores
                        if s.get("campaignFamily") == candidate.campaign_family
                    ),
                    None,
                )
                qual_summary = QualificationSummary(
                    adjusted_score=(primary_score.get("adjustedScore") if primary_score else None),
                    recommended_families=qual_json.get("recommendedFamilies", []),
                    qualified_at=qual_json.get("qualifiedAt"),
                    ruleset_versions_used=qual_json.get("rulesetVersionsUsed", {}),
                )

    # Load linked content assets
    links_result = await session.execute(
        select(ContentAssetLink).where(ContentAssetLink.candidate_id == candidate_id)
    )
    link_rows = list(links_result.scalars().all())
    linked_assets: list[AssetContext] = []
    for link in link_rows:
        asset = await session.get(ContentAsset, link.asset_id)
        if asset is not None:
            linked_assets.append(
                AssetContext(
                    asset_id=asset.id,
                    asset_type=asset.asset_type,
                    summary=asset.summary,
                    link_role=link.link_role,
                )
            )

    # Build candidate input
    metrics = candidate.metrics_json or {}
    candidate_input = CandidateInput(
        id=candidate.id,
        campaign_family=candidate.campaign_family,
        decision_state=candidate.decision_state,
        metrics_json=metrics if isinstance(metrics, dict) else {},
        deliverables=candidate.deliverables,
        owner_user_id=candidate.owner_user_id,
    )

    # Assemble brief (pure, no DB)
    brief = assemble_brief(
        candidate=candidate_input,
        signals=signals,
        qualification_summary=qual_summary,
        linked_assets=linked_assets,
    )

    # Persist as a new campaign_briefs row (append-only)
    brief_row = await create_campaign_brief(
        session,
        candidate_id=candidate_id,
        content=brief.to_dict(),
        generated_by="c3_assembler",
    )
    await session.commit()
    await session.refresh(brief_row)

    return {
        "brief": {
            "id": brief_row.id,
            "candidateId": brief_row.candidate_id,
            "generatedAt": brief_row.generated_at.isoformat(),
            "generatedBy": brief_row.generated_by,
            "content": brief_row.content,
        }
    }


@router.post("/candidates/{candidate_id}/advance")
async def advance_candidate(
    candidate_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Advance a campaign candidate (approve / reject / monitor / request_changes).

    Mirrors the Node app's POST /candidates/:id/decision endpoint.
    """
    body = body or {}
    action = _opt_str(body.get("action"))
    if not action or action not in _VALID_ACTIONS:
        raise bad_request(
            f"action must be one of: {', '.join(sorted(_VALID_ACTIONS))}",
            "campaign_ops_action_invalid",
        )

    try:
        candidate = await get_candidate(session, candidate_id)
    except ValueError:
        raise not_found("Campaign candidate not found", "campaign_ops_candidate_not_found")  # noqa: B904

    new_state = _ACTION_STATE_MAP[action]
    await transition(session, "brief", candidate_id, new_state)

    await session.commit()
    await session.refresh(candidate)
    return _serialize_candidate(candidate)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _opt_str(value: Any) -> str | None:
    return str(value).strip() if isinstance(value, str) and value.strip() else None


def _serialize_candidate(c: CampaignCandidate) -> dict[str, Any]:
    return {
        "id": c.id,
        "campaignFamily": c.campaign_family,
        "sourceSignalId": c.source_signal_id,
        "name": c.name,
        "objective": c.objective,
        "initiatedAt": c.initiated_at.isoformat() if c.initiated_at else None,
        "initiatedBy": c.initiated_by,
        "predecessorId": c.predecessor_id,
        "initiationProposalJson": c.initiation_proposal_json,
        "stage": c.stage,
        "decisionState": c.decision_state,
        "workspaceState": c.workspace_state,
        "rulesetVersionAtQualification": c.ruleset_version_at_qualification,
        "metricsJson": c.metrics_json,
        "deliverables": c.deliverables,
        "ownerUserId": c.owner_user_id,
        "targetScopeJson": c.target_scope_json,
        "deliverableTypesJson": c.deliverable_types_json,
        "createdAt": c.created_at.isoformat(),
        "updatedAt": c.updated_at.isoformat(),
    }
