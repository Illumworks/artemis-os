"""WebSocket identity resolution for the collab endpoint.

Mirrors the trust boundary in `artemis.identity.dependencies.resolve_request_identity`
but adapted for WebSocket upgrade (no Request object, no per-request caching).
"""

from __future__ import annotations

from fastapi import WebSocket

from artemis.config import settings
from artemis.identity.cf_access import (
    CfAccessConfigurationError,
    CfAccessVerificationError,
    get_cf_access_verifier,
)
from artemis.identity.dependencies import (
    _DEV_USER_EMAIL,  # noqa: PLC2701
    _DEV_USER_NAME,  # noqa: PLC2701
    RequestIdentity,
)


async def resolve_ws_identity(websocket: WebSocket) -> RequestIdentity | None:
    """Return the trusted identity for an upgrading WebSocket connection.

    Returns ``None`` when CF Access is enabled and the JWT is absent or invalid;
    the caller is responsible for closing the connection with code 4401.

    In dev mode (cf_access_enabled=False) a shim identity is returned.  Two
    distinct query-param overrides let local test windows impersonate different
    users without running Cloudflare in front:
        ?as_email=alice@example.com&as_name=Alice
    """
    if not settings.cf_access_enabled:
        email = websocket.query_params.get("as_email") or _DEV_USER_EMAIL
        name = websocket.query_params.get("as_name") or _DEV_USER_NAME
        return RequestIdentity(
            email=email,
            name=name,
            claims={"mode": "dev_shim"},
            source="dev_shim",
        )

    token = websocket.headers.get("cf-access-jwt-assertion")
    if not token:
        return None

    try:
        verified = await get_cf_access_verifier().verify_jwt(token)
    except (CfAccessVerificationError, CfAccessConfigurationError):
        return None

    return RequestIdentity(
        email=verified.email,
        name=verified.name,
        claims=verified.claims,
        source="cloudflare_access",
    )
