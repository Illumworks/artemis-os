"""Shared bearer-token auth dependency.

If settings.token is None (default single-user dev mode), all requests are
allowed through. If set, the Authorization header must carry a matching token.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from artemis.config import settings
from artemis.identity.dependencies import resolve_request_identity


async def require_token(
    request: Request,
    authorization: str | None = Header(default=None),
    cf_access_jwt_assertion: str | None = Header(default=None, alias="Cf-Access-Jwt-Assertion"),
) -> None:
    """FastAPI dependency: auth gate for shared-token or Cloudflare Access mode."""
    if settings.cf_access_enabled:
        await resolve_request_identity(request, cf_access_jwt_assertion)
        return
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
