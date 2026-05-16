"""Repository helpers for the OKR Studio domain.

All functions are async and accept a SQLAlchemy AsyncSession.
Callers own commit / rollback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from artemis.okr.models import (
    OkrActivity,
    OkrKeyResult,
    OkrNextUp,
    OkrObjective,
    OkrUpdatePreview,
)

# ── Objectives ────────────────────────────────────────────────────────────────


async def list_objectives(
    session: AsyncSession,
    *,
    cycle: str | None = None,
    include_archived: bool = False,
) -> list[OkrObjective]:
    """Return objectives with eager-loaded key results, ordered by sort_order."""
    q = (
        select(OkrObjective)
        .options(selectinload(OkrObjective.key_results))
        .order_by(OkrObjective.sort_order, OkrObjective.id)
    )
    if cycle:
        q = q.where(OkrObjective.cycle == cycle)
    if not include_archived:
        q = q.where(OkrObjective.archived_at.is_(None))
    result = await session.execute(q)
    return list(result.scalars().unique())


async def get_objective(session: AsyncSession, objective_id: int) -> OkrObjective | None:
    result = await session.execute(
        select(OkrObjective)
        .options(selectinload(OkrObjective.key_results))
        .where(OkrObjective.id == objective_id)
    )
    return result.scalar_one_or_none()


async def create_objective(session: AsyncSession, **kwargs: Any) -> OkrObjective:
    obj = OkrObjective(**kwargs)
    session.add(obj)
    await session.flush()
    await session.refresh(obj)
    return obj


async def update_objective(
    session: AsyncSession, objective_id: int, **kwargs: Any
) -> OkrObjective | None:
    obj = await get_objective(session, objective_id)
    if obj is None:
        return None
    for key, value in kwargs.items():
        setattr(obj, key, value)
    obj.updated_at = datetime.now(UTC)
    await session.flush()
    return obj


async def delete_objective(session: AsyncSession, objective_id: int) -> bool:
    obj = await session.get(OkrObjective, objective_id)
    if obj is None:
        return False
    await session.delete(obj)
    await session.flush()
    return True


# ── Key Results ───────────────────────────────────────────────────────────────


async def list_key_results(session: AsyncSession, objective_id: int) -> list[OkrKeyResult]:
    result = await session.execute(
        select(OkrKeyResult)
        .where(OkrKeyResult.objective_id == objective_id)
        .order_by(OkrKeyResult.sort_order, OkrKeyResult.id)
    )
    return list(result.scalars())


async def get_key_result(session: AsyncSession, kr_id: int) -> OkrKeyResult | None:
    return await session.get(OkrKeyResult, kr_id)


async def create_key_result(session: AsyncSession, **kwargs: Any) -> OkrKeyResult:
    kr = OkrKeyResult(**kwargs)
    session.add(kr)
    await session.flush()
    await session.refresh(kr)
    return kr


async def update_key_result(
    session: AsyncSession, kr_id: int, **kwargs: Any
) -> OkrKeyResult | None:
    kr = await get_key_result(session, kr_id)
    if kr is None:
        return None
    for key, value in kwargs.items():
        setattr(kr, key, value)
    kr.updated_at = datetime.now(UTC)
    await session.flush()
    return kr


async def delete_key_result(session: AsyncSession, kr_id: int) -> bool:
    kr = await session.get(OkrKeyResult, kr_id)
    if kr is None:
        return False
    await session.delete(kr)
    await session.flush()
    return True


# ── Activity ──────────────────────────────────────────────────────────────────


async def list_activity(
    session: AsyncSession,
    *,
    kr_id: int | None = None,
    limit: int = 50,
) -> list[OkrActivity]:
    q = select(OkrActivity).order_by(OkrActivity.created_at.desc()).limit(limit)
    if kr_id is not None:
        q = q.where(OkrActivity.kr_id == kr_id)
    result = await session.execute(q)
    return list(result.scalars())


async def create_activity(session: AsyncSession, **kwargs: Any) -> OkrActivity:
    act = OkrActivity(**kwargs)
    session.add(act)
    await session.flush()
    await session.refresh(act)
    return act


# ── Next Up ───────────────────────────────────────────────────────────────────


async def list_next_up(
    session: AsyncSession, *, include_dismissed: bool = False
) -> list[OkrNextUp]:
    q = select(OkrNextUp).order_by(OkrNextUp.sort_order, OkrNextUp.id)
    if not include_dismissed:
        q = q.where(OkrNextUp.dismissed_at.is_(None))
    result = await session.execute(q)
    return list(result.scalars())


async def get_next_up_item(session: AsyncSession, item_id: int) -> OkrNextUp | None:
    return await session.get(OkrNextUp, item_id)


async def create_next_up(session: AsyncSession, **kwargs: Any) -> OkrNextUp:
    item = OkrNextUp(**kwargs)
    session.add(item)
    await session.flush()
    await session.refresh(item)
    return item


async def update_next_up(session: AsyncSession, item_id: int, **kwargs: Any) -> OkrNextUp | None:
    item = await get_next_up_item(session, item_id)
    if item is None:
        return None
    for key, value in kwargs.items():
        setattr(item, key, value)
    await session.flush()
    return item


async def dismiss_next_up(session: AsyncSession, item_id: int) -> OkrNextUp | None:
    return await update_next_up(session, item_id, dismissed_at=datetime.now(UTC))


async def delete_next_up(session: AsyncSession, item_id: int) -> bool:
    item = await session.get(OkrNextUp, item_id)
    if item is None:
        return False
    await session.delete(item)
    await session.flush()
    return True


# ── Update Previews ───────────────────────────────────────────────────────────


async def create_update_preview(session: AsyncSession, **kwargs: Any) -> OkrUpdatePreview:
    preview = OkrUpdatePreview(**kwargs)
    session.add(preview)
    await session.flush()
    await session.refresh(preview)
    return preview


async def commit_update_preview(session: AsyncSession, preview_id: int) -> OkrUpdatePreview | None:
    preview = await session.get(OkrUpdatePreview, preview_id)
    if preview is None:
        return None
    preview.committed_at = datetime.now(UTC)
    await session.flush()
    return preview
