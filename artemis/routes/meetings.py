"""Meetings router — /api/meetings.

Endpoints:
  GET  /api/meetings/overview  — meeting summary (Granola-backed; not yet connected)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from artemis.marketing.routes._auth import require_token

router = APIRouter(
    prefix="/api/meetings",
    tags=["meetings"],
    dependencies=[Depends(require_token)],
)


@router.get("/overview")
async def get_meetings_overview() -> dict[str, Any]:
    """Return meetings overview.

    Granola integration is scheduled for J5. Always returns not_connected for now.
    """
    return {"status": "not_connected", "provider": "granola"}
