"""Current-user identity routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from artemis.identity.dependencies import get_current_user, resolve_request_identity
from artemis.identity.models import User
from artemis.identity.schemas import CurrentUserRead
from artemis.identity.scope_policy import OWNER_EMAIL

router = APIRouter(tags=["identity"])


def _serialize_user(user: User, *, is_owner: bool = False) -> CurrentUserRead:
    return CurrentUserRead(id=user.id, email=user.email, name=user.name, is_owner=is_owner)


def _resolve_is_owner(email: str | None) -> bool:
    """Return True iff *email* matches the owner.  Fail-closed: None/blank → False."""
    if not email or not isinstance(email, str):
        return False
    return email.strip().lower() == OWNER_EMAIL


@router.get("/api/me", response_model=CurrentUserRead)
async def get_me(
    request: Request,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> CurrentUserRead:
    try:
        identity = await resolve_request_identity(request)
        is_owner = _resolve_is_owner(identity.email)
    except Exception:
        # Fail-closed: if identity resolution fails, treat as non-owner.
        is_owner = False
    return _serialize_user(current_user, is_owner=is_owner)


@router.get("/api/account", response_model=CurrentUserRead)
async def get_account(
    request: Request,
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> CurrentUserRead:
    try:
        identity = await resolve_request_identity(request)
        is_owner = _resolve_is_owner(identity.email)
    except Exception:
        is_owner = False
    return _serialize_user(current_user, is_owner=is_owner)
