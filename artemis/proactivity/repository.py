"""Repository helpers for proactive scheduled delivery state and commitments."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity.models import Commitment, MorningBriefDelivery, OkrCheckinBreadcrumb

_DELIVERY_KIND = "morning_brief"
_OKR_CHECKIN_KIND = "okr_checkin"
_PROVIDER = "slack"


async def reserve_morning_brief_delivery(
    session: AsyncSession,
    *,
    recipient_id: str,
    delivery_date: date,
) -> tuple[MorningBriefDelivery, bool]:
    """Create the once-per-day reservation row or return the existing row."""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(MorningBriefDelivery)
        .values(
            delivery_kind=_DELIVERY_KIND,
            provider=_PROVIDER,
            recipient_id=recipient_id,
            delivery_date=delivery_date,
            status="reserved",
            reserved_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["delivery_kind", "provider", "recipient_id", "delivery_date"]
        )
        .returning(MorningBriefDelivery.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is not None:
        row = await session.get(MorningBriefDelivery, inserted_id)
        assert row is not None
        return row, True

    result = await session.execute(
        select(MorningBriefDelivery).where(
            MorningBriefDelivery.delivery_kind == _DELIVERY_KIND,
            MorningBriefDelivery.provider == _PROVIDER,
            MorningBriefDelivery.recipient_id == recipient_id,
            MorningBriefDelivery.delivery_date == delivery_date,
        )
    )
    row = result.scalar_one()
    return row, False


async def mark_morning_brief_delivery_sent(
    session: AsyncSession,
    *,
    delivery_id: int,
    snapshot_id: int | None,
) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(MorningBriefDelivery)
        .where(MorningBriefDelivery.id == delivery_id)
        .values(
            status="sent",
            snapshot_id=snapshot_id,
            last_error=None,
            delivered_at=now,
            updated_at=now,
        )
    )


async def mark_morning_brief_delivery_failed(
    session: AsyncSession,
    *,
    delivery_id: int,
    error: str,
) -> None:
    await session.execute(
        update(MorningBriefDelivery)
        .where(MorningBriefDelivery.id == delivery_id)
        .values(
            status="failed",
            last_error=error[:2000],
            updated_at=datetime.now(UTC),
        )
    )


# ── OKR check-in reservation (once-per-Friday idempotency) ───────────────────
# Uses the same MorningBriefDelivery table with delivery_kind='okr_checkin'.
# The delivery_date is set to the Friday itself so the unique constraint
# (delivery_kind, provider, recipient_id, delivery_date) gives once-per-Friday
# idempotency at no extra schema cost.


async def reserve_okr_checkin_delivery(
    session: AsyncSession,
    *,
    recipient_id: str,
    delivery_date: date,
) -> tuple[MorningBriefDelivery, bool]:
    """Create the once-per-Friday OKR check-in reservation or return the existing row.

    Returns (row, created) where created=True on first insert, False on conflict.
    """
    now = datetime.now(UTC)
    stmt = (
        pg_insert(MorningBriefDelivery)
        .values(
            delivery_kind=_OKR_CHECKIN_KIND,
            provider=_PROVIDER,
            recipient_id=recipient_id,
            delivery_date=delivery_date,
            status="reserved",
            reserved_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            index_elements=["delivery_kind", "provider", "recipient_id", "delivery_date"]
        )
        .returning(MorningBriefDelivery.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is not None:
        row = await session.get(MorningBriefDelivery, inserted_id)
        assert row is not None
        return row, True

    result = await session.execute(
        select(MorningBriefDelivery).where(
            MorningBriefDelivery.delivery_kind == _OKR_CHECKIN_KIND,
            MorningBriefDelivery.provider == _PROVIDER,
            MorningBriefDelivery.recipient_id == recipient_id,
            MorningBriefDelivery.delivery_date == delivery_date,
        )
    )
    row = result.scalar_one()
    return row, False


async def mark_okr_checkin_delivery_sent(
    session: AsyncSession,
    *,
    delivery_id: int,
) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(MorningBriefDelivery)
        .where(MorningBriefDelivery.id == delivery_id)
        .values(
            status="sent",
            last_error=None,
            delivered_at=now,
            updated_at=now,
        )
    )


async def mark_okr_checkin_delivery_failed(
    session: AsyncSession,
    *,
    delivery_id: int,
    error: str,
) -> None:
    await session.execute(
        update(MorningBriefDelivery)
        .where(MorningBriefDelivery.id == delivery_id)
        .values(
            status="failed",
            last_error=error[:2000],
            updated_at=datetime.now(UTC),
        )
    )


# ── OKR check-in breadcrumbs ─────────────────────────────────────────────────
# A breadcrumb is left when a Friday check-in is posted.  handle_turn reads it
# to inject OKR-reconcile context for the recipient's next DM turn(s).
# Breadcrumbs expire on TTL (end of following Monday) and can be marked complete.
# Rows are lossless: never deleted, only superseded by completed_at or expires_at.


async def create_okr_checkin_breadcrumb(
    session: AsyncSession,
    *,
    recipient_id: str,
    kr_snapshot: list[dict[str, Any]],
    proposal_text: str,
    expires_at: datetime,
) -> OkrCheckinBreadcrumb:
    """Insert a new OKR check-in breadcrumb."""
    crumb = OkrCheckinBreadcrumb(
        recipient_id=recipient_id,
        kr_snapshot=kr_snapshot,
        proposal_text=proposal_text,
        expires_at=expires_at,
    )
    session.add(crumb)
    await session.flush()
    await session.refresh(crumb)
    return crumb


async def get_live_okr_checkin_breadcrumb(
    session: AsyncSession,
    recipient_id: str,
) -> OkrCheckinBreadcrumb | None:
    """Return the most recent live breadcrumb for a recipient, or None.

    A breadcrumb is "live" when:
    - expires_at > now (not expired)
    - completed_at IS NULL (not yet completed)
    """
    now = datetime.now(UTC)
    result = await session.execute(
        select(OkrCheckinBreadcrumb)
        .where(
            OkrCheckinBreadcrumb.recipient_id == recipient_id,
            OkrCheckinBreadcrumb.expires_at > now,
            OkrCheckinBreadcrumb.completed_at.is_(None),
        )
        .order_by(OkrCheckinBreadcrumb.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def complete_okr_checkin_breadcrumb(
    session: AsyncSession,
    breadcrumb_id: int,
) -> None:
    """Mark a breadcrumb as completed so it no longer injects context."""
    await session.execute(
        update(OkrCheckinBreadcrumb)
        .where(OkrCheckinBreadcrumb.id == breadcrumb_id)
        .values(completed_at=datetime.now(UTC))
    )


async def set_staged_updates(
    session: AsyncSession,
    breadcrumb_id: int,
    staged: list[dict[str, Any]],
) -> None:
    """Write the staged_updates list to a breadcrumb row.

    Pass an empty list (or None) to clear.  Never deletes the row (lossless).
    """
    await session.execute(
        update(OkrCheckinBreadcrumb)
        .where(OkrCheckinBreadcrumb.id == breadcrumb_id)
        .values(staged_updates=staged or None)
    )


async def clear_staged_updates(
    session: AsyncSession,
    breadcrumb_id: int,
) -> None:
    """Clear staged_updates on a breadcrumb without completing it."""
    await session.execute(
        update(OkrCheckinBreadcrumb)
        .where(OkrCheckinBreadcrumb.id == breadcrumb_id)
        .values(staged_updates=None)
    )


async def upsert_commitment(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: str,
    text: str,
    owner_user_id: int | None,
    due: datetime | None,
    sensitivity: str,
) -> tuple[Commitment, bool]:
    """Insert or refresh a commitment keyed by (source_type, source_id, text)."""
    now = datetime.now(UTC)
    stmt = (
        pg_insert(Commitment)
        .values(
            source_type=source_type,
            source_id=source_id,
            text=text,
            owner_user_id=owner_user_id,
            due=due,
            sensitivity=sensitivity,
            updated_at=now,
        )
        .on_conflict_do_nothing(
            constraint="uq_commitments_source_text",
        )
        .returning(Commitment.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is not None:
        row = await session.get(Commitment, inserted_id)
        assert row is not None
        return row, True

    await session.execute(
        update(Commitment)
        .where(
            Commitment.source_type == source_type,
            Commitment.source_id == source_id,
            Commitment.text == text,
        )
        .values(
            owner_user_id=owner_user_id,
            due=due,
            sensitivity=sensitivity,
            updated_at=now,
        )
    )
    result = await session.execute(
        select(Commitment).where(
            Commitment.source_type == source_type,
            Commitment.source_id == source_id,
            Commitment.text == text,
        )
    )
    row = result.scalar_one()
    return row, False


async def reactivate_expired_snoozes(
    session: AsyncSession,
    *,
    now: datetime,
) -> None:
    """Move expired snoozes back to active so the sweep can see them again."""
    await session.execute(
        update(Commitment)
        .where(
            Commitment.status == "snoozed",
            Commitment.snoozed_until.is_not(None),
            Commitment.snoozed_until <= now,
        )
        .values(
            status="active",
            updated_at=now,
        )
    )


async def list_commitment_followup_candidates(
    session: AsyncSession,
    *,
    now: datetime,
    due_soon_cutoff: datetime,
    renotify_cutoff: datetime,
) -> list[Commitment]:
    """Return commitments eligible for a proactive follow-up sweep."""
    result = await session.execute(
        select(Commitment)
        .where(
            Commitment.status == "active",
            or_(Commitment.snoozed_until.is_(None), Commitment.snoozed_until <= now),
            or_(
                Commitment.last_notified_at.is_(None),
                Commitment.last_notified_at <= renotify_cutoff,
            ),
            or_(
                Commitment.due.is_not(None) & (Commitment.due <= due_soon_cutoff),
                Commitment.last_notified_at.is_(None),
            ),
        )
        .order_by(
            Commitment.due.asc().nulls_last(), Commitment.created_at.asc(), Commitment.id.asc()
        )
    )
    return list(result.scalars().all())


async def mark_commitment_notified(
    session: AsyncSession,
    *,
    commitment_id: int,
    notified_at: datetime,
) -> None:
    await session.execute(
        update(Commitment)
        .where(Commitment.id == commitment_id)
        .values(last_notified_at=notified_at, updated_at=notified_at)
    )


async def get_commitment(session: AsyncSession, commitment_id: int) -> Commitment | None:
    return await session.get(Commitment, commitment_id)


async def mark_commitment_done(
    session: AsyncSession,
    *,
    commitment_id: int,
    now: datetime,
) -> Commitment | None:
    await session.execute(
        update(Commitment)
        .where(Commitment.id == commitment_id)
        .values(
            status="done",
            snoozed_until=None,
            updated_at=now,
        )
    )
    return await session.get(Commitment, commitment_id)


async def snooze_commitment(
    session: AsyncSession,
    *,
    commitment_id: int,
    snoozed_until: datetime,
    now: datetime,
) -> Commitment | None:
    await session.execute(
        update(Commitment)
        .where(Commitment.id == commitment_id)
        .values(
            status="snoozed",
            snoozed_until=snoozed_until,
            updated_at=now,
        )
    )
    return await session.get(Commitment, commitment_id)


async def dismiss_commitment(
    session: AsyncSession,
    *,
    commitment_id: int,
    now: datetime,
) -> Commitment | None:
    """Move a commitment to the terminal 'dismissed' state.

    Distinct from 'done': dismissed means irrelevant/never-happened, not
    completed.  No further follow-up nags are ever sent for dismissed
    commitments; they are also excluded from re-ingest via the dismissals table.
    """
    await session.execute(
        update(Commitment)
        .where(Commitment.id == commitment_id)
        .values(
            status="dismissed",
            snoozed_until=None,
            updated_at=now,
        )
    )
    return await session.get(Commitment, commitment_id)


async def find_commitment_by_source_and_text(
    session: AsyncSession,
    *,
    source_type: str,
    source_id: str,
    text: str,
) -> Commitment | None:
    """Look up a commitment by its natural key (source_type, source_id, text)."""
    result = await session.execute(
        select(Commitment).where(
            Commitment.source_type == source_type,
            Commitment.source_id == source_id,
            Commitment.text == text,
        )
    )
    return result.scalar_one_or_none()
