"""Repository helpers for morning brief delivery state."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity.models import MorningBriefDelivery

_DELIVERY_KIND = "morning_brief"
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
