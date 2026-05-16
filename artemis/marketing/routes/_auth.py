"""Shared bearer-token auth dependency.

If settings.token is None (default single-user dev mode), all requests are
allowed through. If set, the Authorization header must carry a matching token.
"""

from __future__ import annotations

from fastapi import Header, HTTPException

from artemis.config import settings


async def require_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency: validate bearer token if ARTEMIS_TOKEN is configured."""
    if settings.token is None:
        # Dev mode — no auth required
        return
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail={"error": "Authorization header required", "code": "unauthorized"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != settings.token:
        raise HTTPException(
            status_code=401,
            detail={"error": "Invalid or expired token", "code": "unauthorized"},
        )
