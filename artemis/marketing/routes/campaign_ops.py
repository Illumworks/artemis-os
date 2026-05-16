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
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import CampaignCandidate
from artemis.marketing.repository import (
    get_candidate,
    list_candidates,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found

router = APIRouter(
    prefix="/api/campaign-ops",
    tags=["campaign-ops"],
    dependencies=[Depends(require_token)],
)

_VALID_ACTIONS = {"approve", "reject", "monitor", "request_changes"}

# Decision state transitions (mirrors Node's applyCampaignCandidateAction)
_ACTION_STATE_MAP = {
    "approve": "approved",
    "reject": "rejected",
    "monitor": "monitoring",
    "request_changes": "changes_requested",
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
async def assemble_brief(
    candidate_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Brief assembly stub — C3 replaces this with the real assembler port."""
    try:
        await get_candidate(session, candidate_id)
    except ValueError:
        raise not_found("Campaign candidate not found", "campaign_ops_candidate_not_found")  # noqa: B904
    return {"stub": True}


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
    candidate.decision_state = new_state

    from datetime import UTC, datetime

    candidate.updated_at = datetime.now(tz=UTC)

    await session.flush()
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
        "stage": c.stage,
        "decisionState": c.decision_state,
        "workspaceState": c.workspace_state,
        "rulesetVersionAtQualification": c.ruleset_version_at_qualification,
        "metricsJson": c.metrics_json,
        "deliverables": c.deliverables,
        "ownerUserId": c.owner_user_id,
        "createdAt": c.created_at.isoformat(),
        "updatedAt": c.updated_at.isoformat(),
    }
