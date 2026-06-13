"""Repository helpers for the awaiting-reply radar dedup + dismiss ledger."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity.models import RadarSurfacedItem

# Re-nag window: only surface an item again after this many hours.
_RENOTIFY_HOURS: int = 24


async def upsert_surfaced(
    session: AsyncSession,
    *,
    item_type: str,
    item_key: str,
    label: str,
    permalink: str | None,
    now: datetime | None = None,
) -> tuple[RadarSurfacedItem, bool]:
    """Insert or update a surfaced-item row.

    Returns ``(row, is_new)``.  ``is_new`` is True when this is the first time
    the item has ever been surfaced.  The ``last_surfaced_at`` column is
    updated on every call (even for existing rows) so the re-nag cadence works.

    Dismissed rows are returned as-is without updating timestamps — the caller
    checks ``dismissed_at`` and skips them.
    """
    now = now or datetime.now(UTC)
    # Try an upsert.  If the row already exists we want to update last_surfaced_at
    # but only when NOT dismissed.
    stmt = (
        pg_insert(RadarSurfacedItem.__table__)  # type: ignore[arg-type]
        .values(
            item_type=item_type,
            item_key=item_key,
            label=label,
            permalink=permalink,
            first_surfaced_at=now,
            last_surfaced_at=now,
            dismissed_at=None,
        )
        .on_conflict_do_update(
            index_elements=["item_type", "item_key"],
            set_={
                "label": label,
                "permalink": permalink,
                "last_surfaced_at": now,
            },
            where=RadarSurfacedItem.__table__.c.dismissed_at.is_(None),
        )
        .returning(RadarSurfacedItem.__table__.c.id)
    )
    result = await session.execute(stmt)
    row_id = result.scalar_one_or_none()
    if row_id is None:
        # Row exists but is dismissed — fetch it without touching it.
        fetch = await session.execute(
            select(RadarSurfacedItem).where(
                RadarSurfacedItem.item_type == item_type,
                RadarSurfacedItem.item_key == item_key,
            )
        )
        existing = fetch.scalar_one()
        return existing, False

    fetch = await session.execute(
        select(RadarSurfacedItem)
        .where(RadarSurfacedItem.id == row_id)
        .execution_options(populate_existing=True)
    )
    row = fetch.scalar_one()
    is_new = row.first_surfaced_at >= now - __import__("datetime").timedelta(seconds=1)
    return row, is_new


async def list_due_for_surface(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    renotify_hours: int = _RENOTIFY_HOURS,
) -> list[RadarSurfacedItem]:
    """Return non-dismissed items that are due to be surfaced.

    Due = never surfaced yet, or last_surfaced_at older than renotify_hours.
    """
    now = now or datetime.now(UTC)
    from datetime import timedelta

    cutoff = now - timedelta(hours=renotify_hours)
    result = await session.execute(
        select(RadarSurfacedItem).where(
            RadarSurfacedItem.dismissed_at.is_(None),
            RadarSurfacedItem.last_surfaced_at <= cutoff,
        )
    )
    return list(result.scalars().all())


async def get_by_type_and_key(
    session: AsyncSession,
    item_type: str,
    item_key: str,
) -> RadarSurfacedItem | None:
    result = await session.execute(
        select(RadarSurfacedItem).where(
            RadarSurfacedItem.item_type == item_type,
            RadarSurfacedItem.item_key == item_key,
        )
    )
    return result.scalar_one_or_none()


async def dismiss_item(
    session: AsyncSession,
    *,
    item_type: str,
    item_key: str,
    now: datetime | None = None,
) -> RadarSurfacedItem | None:
    """Mark a radar item as dismissed.  Returns the updated row, or None if not found."""
    now = now or datetime.now(UTC)
    result = await session.execute(
        update(RadarSurfacedItem)
        .where(
            RadarSurfacedItem.item_type == item_type,
            RadarSurfacedItem.item_key == item_key,
        )
        .values(dismissed_at=now)
        .returning(RadarSurfacedItem)
    )
    return result.scalar_one_or_none()


async def dismiss_by_id(
    session: AsyncSession,
    *,
    item_id: int,
    now: datetime | None = None,
) -> RadarSurfacedItem | None:
    """Mark a radar item dismissed by its integer PK."""
    now = now or datetime.now(UTC)
    result = await session.execute(
        update(RadarSurfacedItem)
        .where(RadarSurfacedItem.id == item_id)
        .values(dismissed_at=now)
        .returning(RadarSurfacedItem)
    )
    return result.scalar_one_or_none()
