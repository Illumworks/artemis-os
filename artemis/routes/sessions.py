"""Sessions router — /api/sessions.

V1 stub: returns an empty list. Sessions concept maps to fa_sessions, but the
frontend expects the Node app shape which will be wired in a future slice.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from artemis.marketing.routes._auth import require_token

router = APIRouter(tags=["sessions"])


@router.get("/api/sessions")
async def list_sessions(
    project_path: str | None = Query(default=None),
    _: None = Depends(require_token),  # noqa: B008
) -> list[object]:
    """Return empty sessions list (stub — real data wired in a future slice)."""
    return []
