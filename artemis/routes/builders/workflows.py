"""Deprecated Workflows routes — sunset in PIPE6."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from artemis.marketing.routes._auth import require_token

router = APIRouter(
    prefix="/api/workflows",
    tags=["workflows"],
    dependencies=[Depends(require_token)],
)

_DETAIL: dict[str, Any] = {
    "error": "workflows_deprecated",
    "message": (
        "The Workflows surface was sunset in PIPE6 (2026-05-30). "
        "Use Pipelines with sequential edges instead. "
        "See docs/ARTEMIS-OS-MASTER-PLAN.md D6 lock for rationale."
    ),
    "redirect_to": "/api/pipelines",
}


@router.api_route("", methods=["GET", "POST", "PATCH", "DELETE"])
@router.api_route("/", methods=["GET", "POST", "PATCH", "DELETE"])
@router.api_route("/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
async def workflows_deprecated(path: str | None = None) -> None:
    """Return an intentional deprecation signal for legacy workflow calls."""
    raise HTTPException(status_code=410, detail=_DETAIL)
