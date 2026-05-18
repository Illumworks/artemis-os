"""Jira router — /api/jira.

Jira integration is on the J4 roadmap; all endpoints are stubs until then.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from artemis.marketing.routes._auth import require_token

router = APIRouter(prefix="/api/jira", tags=["jira"])


@router.get("/overview")
async def jira_overview(
    _: None = Depends(require_token),  # noqa: B008
) -> dict[str, str]:
    """Return not-connected status until J4 Jira integration is implemented."""
    return {"status": "not_connected", "provider": "jira"}
