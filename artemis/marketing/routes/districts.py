"""District search endpoint — /api/marketing/districts."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import District
from artemis.marketing.routes._auth import require_token

router = APIRouter(
    prefix="/api/marketing/districts",
    tags=["marketing-campaigns"],
    dependencies=[Depends(require_token)],
)


@router.get("/search")
async def search_districts(
    q: str = Query(default="", description="Name substring to search (case-insensitive)"),
    state: str | None = Query(default=None, description="Optional 2-letter state filter"),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[dict[str, Any]]:
    """Typeahead search for districts by name.

    Returns up to `limit` districts whose name matches %q% (ILIKE), optionally
    filtered to a specific state. Results are ordered by name.
    """
    stmt = select(District).where(District.name.ilike(f"%{q}%"))
    if state:
        stmt = stmt.where(District.state == state.upper())
    stmt = stmt.order_by(District.name).limit(limit)

    result = await session.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "state": row.state,
            "tier": row.tier,
            "supported": row.supported,
        }
        for row in rows
    ]
