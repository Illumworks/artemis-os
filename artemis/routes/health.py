"""Health and readiness endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis
from artemis.db import get_session

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Liveness probe — process is alive and routing."""
    return {"ok": True}


@router.get("/readyz")
async def readyz(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Readiness probe — process can serve requests (DB reachable)."""
    try:
        result = await session.execute(text("SELECT 1"))
        result.scalar_one()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"database unreachable: {exc}",
        ) from exc
    return {"ok": True, "db": "reachable"}


@router.get("/api/version")
async def version() -> dict[str, Any]:
    """Return running application version."""
    return {"version": artemis.__version__}
