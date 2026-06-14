"""Shared bearer-token auth dependency.

If settings.token is None (default single-user dev mode), all requests are
allowed through. If set, the Authorization header must carry a matching token.
"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from artemis.config import settings
from artemis.identity.dependencies import resolve_request_identity
from artemis.identity.scope_policy import OWNER_EMAIL


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


async def require_owner(
    request: Request,
    cf_access_jwt_assertion: str | None = Header(default=None, alias="Cf-Access-Jwt-Assertion"),
) -> None:
    """FastAPI dependency: restrict a route to the OWNER only (M3 / D8).

    Owner-only admin surfaces (e.g. the agent builder) must not be reachable by
    marketing teammates. In single-user dev mode (Cloudflare Access disabled) the
    sole local user is treated as the owner so local dev + tests keep working.
    With Cloudflare Access enabled, the verified identity email must match the
    owner. FAIL CLOSED: any unresolved/non-owner identity is rejected.
    """
    if not settings.cf_access_enabled:
        # Dev mode — single local user is the owner.
        return
    identity = await resolve_request_identity(request, cf_access_jwt_assertion)
    if identity.email.strip().lower() != OWNER_EMAIL:
        raise HTTPException(
            status_code=403,
            detail={"error": "Owner-only resource", "code": "forbidden"},
        )
