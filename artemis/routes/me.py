"""Current-user identity routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.identity.schemas import CurrentUserRead

router = APIRouter(tags=["identity"])


def _serialize_user(user: User) -> CurrentUserRead:
    return CurrentUserRead(id=user.id, email=user.email, name=user.name)


@router.get("/api/me", response_model=CurrentUserRead)
async def get_me(current_user: User = Depends(get_current_user)) -> CurrentUserRead:  # noqa: B008
    return _serialize_user(current_user)


@router.get("/api/account", response_model=CurrentUserRead)
async def get_account(current_user: User = Depends(get_current_user)) -> CurrentUserRead:  # noqa: B008
    return _serialize_user(current_user)
