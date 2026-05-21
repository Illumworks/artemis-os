"""Notifications router — /api/notifications.

V1 stub: no notifications table exists yet. All endpoints return empty results.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from artemis.marketing.routes._auth import require_token

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("/history")
async def notifications_history(
    limit: int = Query(default=20),
    offset: int = Query(default=0),
    unread_only: bool = Query(default=False),
    type: str = Query(default=""),
    _: None = Depends(require_token),  # noqa: B008
) -> list[object]:
    """Return empty notification history (stub — no notifications table in V1)."""
    return []


@router.get("/unread-count")
async def unread_count() -> dict[str, int]:
    """Return unread notification count (stub — always 0 until notifications system ships)."""
    return {"count": 0}
