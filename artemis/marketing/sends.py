"""Send pipeline functions for SEND2-B.

Recipient resolution, enqueue, and mark-sent operations for campaign sends.
All functions are async; the caller owns commit/rollback.

NO REAL EMAIL — transport is stubbed. The 'stub' transport only writes
to transport_log; no external email system is called.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.contacts import list_active_contacts_for_districts
from artemis.marketing.models import (
    CampaignCandidate,
    CampaignDeliverable,
    CampaignSend,
    District,
)
from artemis.marketing.state_machine import DeliverableState, transition

logger = logging.getLogger(__name__)


async def resolve_district_ids_for_candidate(
    session: AsyncSession,
    candidate: CampaignCandidate,
) -> list[int]:
    """Return district ids that match the candidate's target_scope_json.

    Modes:
      - all_districts    → all districts where supported=True
      - states           → districts where state in scope.states AND supported=True
      - district_tier    → districts where tier in scope.tiers AND supported=True
      - named_districts  → districts where id in scope.district_ids (no supported filter)

    If target_scope_json is missing/null, falls back to the candidate's source
    signal resolved_district_id (one-district fallback).

    Returns a deduplicated, sorted list of district ids.
    """
    scope: dict[str, Any] | None = candidate.target_scope_json

    if not scope:
        # Fallback: use the signal's resolved_district_id if available
        from sqlalchemy import select as _select

        from artemis.marketing.models import SignalQueue

        if candidate.source_signal_id is not None:
            result = await session.execute(
                _select(SignalQueue.resolved_district_id).where(
                    SignalQueue.id == candidate.source_signal_id
                )
            )
            resolved_id: int | None = result.scalar_one_or_none()
            if resolved_id is not None:
                return [resolved_id]
        return []

    mode = scope.get("mode", "")

    if mode == "all_districts":
        result = await session.execute(
            select(District.id).where(District.supported.is_(True)).order_by(District.id)
        )
        return sorted({x for x in result.scalars().all() if x is not None})

    if mode == "states":
        states: list[str] = scope.get("states") or []
        if not states:
            return []
        result = await session.execute(
            select(District.id)
            .where(District.state.in_(states), District.supported.is_(True))
            .order_by(District.id)
        )
        return sorted({x for x in result.scalars().all() if x is not None})

    if mode == "district_tier":
        tiers: list[str] = scope.get("tiers") or []
        if not tiers:
            return []
        result = await session.execute(
            select(District.id)
            .where(District.tier.in_(tiers), District.supported.is_(True))
            .order_by(District.id)
        )
        return sorted({x for x in result.scalars().all() if x is not None})

    if mode == "named_districts":
        district_ids: list[int] = scope.get("district_ids") or []
        if not district_ids:
            return []
        # No supported filter for explicitly named districts
        result = await session.execute(
            select(District.id).where(District.id.in_(district_ids)).order_by(District.id)
        )
        return sorted({x for x in result.scalars().all() if x is not None})

    logger.warning(
        "resolve_district_ids_for_candidate: unknown mode %r for candidate_id=%s",
        mode,
        candidate.id,
    )
    return []


async def resolve_recipients_for_candidate(
    session: AsyncSession,
    candidate: CampaignCandidate,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Resolve district ids and contact snapshots for a candidate.

    Returns (district_ids, recipients_snapshot) where recipients_snapshot
    is a list of dicts shaped exactly as stored in campaign_sends.recipients:
      {"contact_id": int, "district_id": int, "name": str,
       "email": str, "title": str|null}

    Uses list_active_contacts_for_districts to fetch active contacts.
    """
    district_ids = await resolve_district_ids_for_candidate(session, candidate)
    if not district_ids:
        return [], []

    contacts = await list_active_contacts_for_districts(session, district_ids)
    snapshot: list[dict[str, Any]] = [
        {
            "contact_id": c.id,
            "district_id": c.district_id,
            "name": c.name,
            "email": c.email,
            "title": c.title,
        }
        for c in contacts
    ]
    return district_ids, snapshot


async def enqueue_send_for_deliverable(
    session: AsyncSession,
    *,
    candidate: CampaignCandidate,
    deliverable: CampaignDeliverable,
    actor: str | None = None,
) -> CampaignSend:
    """Create a campaign_sends row and optionally transition the deliverable.

    Behaviour:
    - Deliverable must be in state 'approved'. Raises ValueError otherwise.
    - If recipients resolve to ≥1:
        * Insert campaign_sends row status='queued', recipients=<snapshot>.
        * Transition deliverable approved → queued_for_send.
        * Return the new row.
    - If recipients resolve to 0:
        * Insert campaign_sends row status='skipped',
          skip_reason='no_contacts_on_file', recipients=[].
        * Do NOT transition the deliverable (stays 'approved').
        * Return the new row.

    This is an append-only operation — the caller owns commit.
    """
    if deliverable.status != DeliverableState.approved.value:
        raise ValueError(
            f"deliverable id={deliverable.id} must be in state 'approved' to enqueue send; "
            f"current state: {deliverable.status!r}"
        )

    _district_ids, recipients_snapshot = await resolve_recipients_for_candidate(session, candidate)

    if recipients_snapshot:
        send = CampaignSend(
            candidate_id=candidate.id,
            deliverable_id=deliverable.id,
            recipients=recipients_snapshot,
            status="queued",
            transport="stub",
            transport_log={},
        )
        session.add(send)
        await session.flush()

        # Transition deliverable: approved → queued_for_send
        await transition(
            session,
            "deliverable",
            deliverable.id,
            DeliverableState.queued_for_send,
            actor=actor,
            reason="send_enqueued",
        )
        logger.info(
            "enqueue_send_for_deliverable: queued send_id=%s deliverable_id=%s "
            "recipient_count=%s actor=%s",
            send.id,
            deliverable.id,
            len(recipients_snapshot),
            actor,
        )
        return send

    # Zero contacts — skipped path
    send = CampaignSend(
        candidate_id=candidate.id,
        deliverable_id=deliverable.id,
        recipients=[],
        status="skipped",
        skip_reason="no_contacts_on_file",
        transport="stub",
        transport_log={},
    )
    session.add(send)
    await session.flush()
    logger.info(
        "enqueue_send_for_deliverable: skipped deliverable_id=%s (no contacts) actor=%s",
        deliverable.id,
        actor,
    )
    return send


async def mark_send_sent(
    session: AsyncSession,
    *,
    send_id: int,
    actor: str,
) -> CampaignSend:
    """Idempotent transition of a queued send to sent.

    - Looks up the send row WITH ROW LOCK.
    - If status != 'queued', raises ValueError with current status.
    - Sets status='sent', sent_at=now(), sent_by=actor.
    - Writes stub transport_log (NO REAL EMAIL).
    - Transitions the linked deliverable queued_for_send → sent via state_machine.
    - Caller owns commit.
    """
    result = await session.execute(
        select(CampaignSend).where(CampaignSend.id == send_id).with_for_update()
    )
    send = result.scalar_one_or_none()
    if send is None:
        raise ValueError(f"campaign_sends id={send_id} not found")

    if send.status != "queued":
        raise ValueError(f"not_queued:{send.status}")

    now = datetime.now(UTC)
    recipients = send.recipients if isinstance(send.recipients, list) else []

    send.status = "sent"
    send.sent_at = now
    send.sent_by = actor
    send.transport_log = {
        "transport": "stub",
        "sent_at": now.isoformat(),
        "actor": actor,
        "recipient_count": len(recipients),
        "note": "NO REAL EMAIL — transport pending ESP",
    }
    send.updated_at = now
    await session.flush()

    # Transition the deliverable: queued_for_send → sent
    await transition(
        session,
        "deliverable",
        send.deliverable_id,
        DeliverableState.sent,
        actor=actor,
        reason="send_completed_stub",
    )
    logger.info(
        "mark_send_sent: send_id=%s deliverable_id=%s actor=%s",
        send_id,
        send.deliverable_id,
        actor,
    )
    return send
