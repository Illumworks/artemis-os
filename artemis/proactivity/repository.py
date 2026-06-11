"""Repository helpers for proactive scheduled delivery state."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity.models import MorningBriefDelivery, OkrCheckinBreadcrumb

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
