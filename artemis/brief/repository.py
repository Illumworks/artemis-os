"""Repository for brief_snapshots — append-only per lossless memory rule."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.brief.models import BriefSnapshot


async def save_brief_snapshot(
    session: AsyncSession,
    *,
    brief_json: dict[str, Any],
    sources_json: dict[str, Any],
    model: str,
    tokens_input: int | None,
    tokens_output: int | None,
) -> BriefSnapshot:
    snapshot = BriefSnapshot(
        brief_json=brief_json,
        sources_json=sources_json,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        generated_at=datetime.now(UTC),
    )
    session.add(snapshot)
    await session.flush()
    await session.refresh(snapshot)
    await session.commit()
    return snapshot


async def get_latest_brief_snapshot(session: AsyncSession) -> BriefSnapshot | None:
    result = await session.execute(
        select(BriefSnapshot).order_by(BriefSnapshot.generated_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def list_brief_snapshots(session: AsyncSession, limit: int = 7) -> list[BriefSnapshot]:
    result = await session.execute(
        select(BriefSnapshot).order_by(BriefSnapshot.generated_at.desc()).limit(limit)
    )
    return list(result.scalars().all())
