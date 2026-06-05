"""Campaign Deliverables router — /api/campaign-deliverables.

Endpoints:
  GET  /{candidate_id}         — list deliverables for a candidate
  POST /                       — create a deliverable (writing-handoff stub)
  POST /{id}/submit-review     — submit for review (status transition)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import CampaignDeliverable
from artemis.marketing.repository import get_candidate
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found
from artemis.marketing.state_machine import DeliverableState, transition
from artemis.writing_rules import repository as wr_repo

router = APIRouter(
    prefix="/api/campaign-deliverables",
    tags=["campaign-deliverables"],
    dependencies=[Depends(require_token)],
)


@router.get("")
async def list_deliverables(
    campaign_id: int | None = Query(default=None, alias="campaignId"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Compat: frontend calls ?campaignId= instead of the path candidate id."""
    if campaign_id is None:
        return []
    return await list_deliverables_for_candidate(campaign_id, session)


@router.get("/{candidate_id}")
async def list_deliverables_for_candidate(
    candidate_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List all deliverables for a campaign candidate."""
    # Verify candidate exists
    try:
        await get_candidate(session, candidate_id)
    except ValueError:
        raise not_found("Campaign candidate not found", "campaign_ops_candidate_not_found")  # noqa: B904

    result = await session.execute(
        select(CampaignDeliverable)
        .where(CampaignDeliverable.candidate_id == candidate_id)
        .order_by(CampaignDeliverable.created_at.desc())
    )
    rows = list(result.scalars().all())
    return [_serialize(r) for r in rows]


@router.post("/", status_code=201)
async def create_deliverable(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a campaign deliverable (writing-handoff stub).

    Returns a stub shape; the real writing-studio invocation is deferred to
    when the Writing Studio Python port is available.
    """
    candidate_id_raw = body.get("candidateId") or body.get("candidate_id")
    if candidate_id_raw is None:
        raise bad_request("candidateId is required", "campaign_deliverables_missing_candidate")  # noqa: B904

    try:
        candidate_id = int(candidate_id_raw)
    except (TypeError, ValueError):
        raise bad_request("candidateId must be an integer", "campaign_deliverables_invalid_id")  # noqa: B904

    try:
        candidate = await get_candidate(session, candidate_id)
    except ValueError:
        raise not_found("Campaign candidate not found", "campaign_ops_candidate_not_found")  # noqa: B904

    candidate_name = candidate.name or candidate.campaign_family or f"Campaign {candidate.id}"
    folder = await wr_repo.get_or_create_folder_by_candidate(
        session,
        candidate_id,
        candidate_name=candidate_name,
    )
    metadata = dict(body.get("metadata") or {})
    metadata["folder_id"] = folder.id
    metadata["folder_name"] = candidate_name

    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=body.get("deliverableId") or body.get("deliverable_id"),
        campaign_id=body.get("campaignId") or body.get("campaign_id") or candidate.campaign_family,
        status=body.get("status", "generating"),
        deliverable_metadata=metadata,
    )
    session.add(deliverable)
    await session.flush()
    await session.refresh(deliverable)
    await session.commit()
    return _serialize(deliverable)


@router.post("/{deliverable_id}/submit-review")
async def submit_review(
    deliverable_id: int,
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Transition a deliverable to review_pending status."""
    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.id == deliverable_id)
    )
    deliverable = result.scalar_one_or_none()
    if deliverable is None:
        raise not_found("Deliverable not found", "campaign_deliverables_not_found")  # noqa: B904

    if body and body.get("metadata"):
        deliverable.deliverable_metadata = {
            **(deliverable.deliverable_metadata or {}),
            **body["metadata"],
        }
        await session.flush()

    await transition(session, "deliverable", deliverable_id, DeliverableState.draft_ready)

    await session.commit()
    await session.refresh(deliverable)
    return _serialize(deliverable)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _serialize(d: CampaignDeliverable) -> dict[str, Any]:
    return {
        "id": d.id,
        "candidateId": d.candidate_id,
        "deliverableId": d.deliverable_id,
        "campaignId": d.campaign_id,
        "status": d.status,
        # Wire name stays 'metadata' for frontend compat (column alias in ORM)
        "metadata": d.deliverable_metadata or {},
        "createdAt": d.created_at.isoformat(),
        "updatedAt": d.updated_at.isoformat(),
    }
