"""Deprecated Automations routes — sunset in PIPE6."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from artemis.marketing.routes._auth import require_token

router = APIRouter(
    tags=["automations"],
    dependencies=[Depends(require_token)],
)

_DETAIL: dict[str, Any] = {
    "error": "automations_deprecated",
    "message": (
        "The Automations surface was sunset in PIPE6 (2026-05-30). "
        "Use Pipelines with trigger nodes instead. "
        "See docs/ARTEMIS-OS-MASTER-PLAN.md D6 lock for rationale."
    ),
    "redirect_to": "/api/pipelines",
}


async def _gone() -> None:
    raise HTTPException(status_code=410, detail=_DETAIL)


@router.api_route(
    "/api/automations",
    methods=["GET", "POST", "PATCH", "DELETE"],
)
@router.api_route(
    "/api/automations/",
    methods=["GET", "POST", "PATCH", "DELETE"],
)
@router.api_route(
    "/api/automations/{path:path}",
    methods=["GET", "POST", "PATCH", "DELETE"],
)
@router.api_route(
    "/api/automation-runs/{path:path}",
    methods=["GET", "POST", "PATCH", "DELETE"],
)
async def automations_deprecated(path: str | None = None) -> None:
    """Return an intentional deprecation signal for legacy automation calls."""
    await _gone()
