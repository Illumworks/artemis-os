"""FastAPI dependencies for request identity and current user resolution."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import get_session
from artemis.identity.cf_access import (
    CfAccessConfigurationError,
    CfAccessVerificationError,
    VerifiedCfAccessIdentity,
    get_cf_access_verifier,
)
from artemis.identity.models import User
from artemis.identity.repository import get_or_create_user

_REQUEST_IDENTITY_STATE_KEY = "_artemis_request_identity"
_DEV_USER_EMAIL = "dev@local"
_DEV_USER_NAME = "Local Dev"


@dataclass(frozen=True)
class RequestIdentity:
    email: str
    name: str | None
    claims: dict[str, object]
    source: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"error": detail, "code": "unauthorized"},
    )


async def resolve_request_identity(
    request: Request,
    cf_access_jwt_assertion: str | None = Header(default=None, alias="Cf-Access-Jwt-Assertion"),
) -> RequestIdentity:
    """Return the trusted request identity.

    When Cloudflare Access verification is enabled, missing or invalid JWTs are
    rejected. In local-dev mode, a fixed shim identity keeps the app usable
    without Cloudflare in front.
    """

    cached = getattr(request.state, _REQUEST_IDENTITY_STATE_KEY, None)
    if isinstance(cached, RequestIdentity):
        return cached

    if not settings.cf_access_enabled:
        identity = RequestIdentity(
            email=_DEV_USER_EMAIL,
            name=_DEV_USER_NAME,
            claims={"mode": "dev_shim"},
            source="dev_shim",
        )
        setattr(request.state, _REQUEST_IDENTITY_STATE_KEY, identity)
        return identity

    if not cf_access_jwt_assertion:
        raise _unauthorized("Cf-Access-Jwt-Assertion header required")

    try:
        verified: VerifiedCfAccessIdentity = await get_cf_access_verifier().verify_jwt(
            cf_access_jwt_assertion
        )
    except CfAccessConfigurationError as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": "Cloudflare Access identity is misconfigured",
                "code": "identity_misconfigured",
            },
        ) from exc
    except CfAccessVerificationError as exc:
        raise _unauthorized("Invalid or expired token") from exc

    identity = RequestIdentity(
        email=verified.email,
        name=verified.name,
        claims=verified.claims,
        source="cloudflare_access",
    )
    setattr(request.state, _REQUEST_IDENTITY_STATE_KEY, identity)
    return identity


async def get_current_user(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    identity: RequestIdentity = Depends(resolve_request_identity),  # noqa: B008
) -> User:
    """Resolve, upsert, and return the current trusted user."""
    user = await get_or_create_user(session, identity.email, identity.name)
    await session.commit()
    await session.refresh(user)
    return user
