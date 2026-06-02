"""Sends router — /api/marketing/sends.

Human-gated outbox for campaign deliverable sends.
NO REAL EMAIL — transport is stubbed (transport='stub').

Endpoints:
  GET  /api/marketing/sends          — list sends (default status=queued)
  GET  /api/marketing/sends/{id}     — single send
  POST /api/marketing/sends/{id}/send — human-gated send action (queued → sent)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import (
    CampaignCandidate,
    CampaignDeliverable,
    CampaignSend,
    District,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import conflict, not_found
from artemis.marketing.sends import mark_send_sent

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/marketing/sends",
    tags=["sends"],
    dependencies=[Depends(require_token)],
)

_VALID_STATUSES = frozenset({"queued", "sent", "failed", "skipped"})


class SendActionRequest(BaseModel):
    actor: str = "operator"


# ── Serializer ────────────────────────────────────────────────────────────────


async def _serialize_send(session: AsyncSession, send: CampaignSend) -> dict[str, Any]:
    """Serialize a CampaignSend to the API wire shape."""
    # Load candidate for name
    candidate_name: str | None = None
    candidate = await session.get(CampaignCandidate, send.candidate_id)
    if candidate is not None:
        candidate_name = candidate.name

    # Load deliverable for type/title preview
    deliverable_title: str | None = None
    deliverable_slug: str | None = None
    draft_preview: str = ""
    deliverable = await session.get(CampaignDeliverable, send.deliverable_id)
    if deliverable is not None:
        meta: dict[str, Any] = (
            deliverable.deliverable_metadata
            if isinstance(deliverable.deliverable_metadata, dict)
            else {}
        )
        deliverable_title = meta.get("externalTitle")
        deliverable_slug = meta.get("deliverableTypeSlug")
        # Latest version content preview (first 400 chars)
        versions: list[dict[str, Any]] = meta.get("versions") or []
        if versions:
            latest = versions[-1]
            content: str = latest.get("content") or ""
            draft_preview = content[:400]

    # Resolve district names from recipients
    recipients: list[dict[str, Any]] = (
        send.recipients if isinstance(send.recipients, list) else []
    )
    district_ids: list[int] = sorted(
        {r["district_id"] for r in recipients if isinstance(r.get("district_id"), int)}
    )
    district_names: list[str] = []
    if district_ids:
        result = await session.execute(
            select(District.id, District.name).where(District.id.in_(district_ids))
        )
        id_to_name: dict[int, str] = {row.id: row.name for row in result}
        district_names = [id_to_name.get(did, str(did)) for did in district_ids]

    return {
        "id": send.id,
        "candidateId": send.candidate_id,
        "candidateName": candidate_name,
        "deliverableId": send.deliverable_id,
        "deliverableTitle": deliverable_title,
        "deliverableSlug": deliverable_slug,
        "draftPreview": draft_preview,
        "recipients": recipients,
        "recipientCount": len(recipients),
        "districtIds": district_ids,
        "districtNames": district_names,
        "status": send.status,
        "skipReason": send.skip_reason,
        "queuedAt": send.queued_at.isoformat() if send.queued_at else None,
        "sentAt": send.sent_at.isoformat() if send.sent_at else None,
        "sentBy": send.sent_by,
        "transport": send.transport,
    }


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("")
@router.get("/")
async def list_sends_route(
    status: str = Query(default="queued"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """List campaign sends.  Default status=queued, sorted by queued_at DESC."""
    q = select(CampaignSend)
    if status:
        q = q.where(CampaignSend.status == status)
    q = q.order_by(CampaignSend.queued_at.desc()).limit(limit)
    result = await session.execute(q)
    rows = result.scalars().all()
    return [await _serialize_send(session, row) for row in rows]


@router.get("/{send_id}")
async def get_send_route(
    send_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single campaign send."""
    send = await session.get(CampaignSend, send_id)
    if send is None:
        raise not_found("Send not found", "send_not_found")  # noqa: B904
    return await _serialize_send(session, send)


@router.post("/{send_id}/send")
async def execute_send_route(
    send_id: int,
    body: SendActionRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Human-gated send action — transitions a queued send to sent.

    NO REAL EMAIL — transport is stubbed; only transport_log is written.
    Returns 409 Conflict if the send is not in 'queued' status.
    """
    # Pre-check existence before acquiring row lock in mark_send_sent
    send = await session.get(CampaignSend, send_id)
    if send is None:
        raise not_found("Send not found", "send_not_found")  # noqa: B904

    if send.status != "queued":
        raise conflict(
            f"send already_{send.status}",
            "send_not_queued",
        )

    try:
        send = await mark_send_sent(session, send_id=send_id, actor=body.actor)
    except ValueError as exc:
        msg = str(exc)
        if msg.startswith("not_queued:"):
            current_status = msg.split(":", 1)[1]
            raise conflict(
                f"send already_{current_status}",
                "send_not_queued",
            ) from exc
        raise

    await session.commit()
    return await _serialize_send(session, send)
