"""Identity directory — teammates listing for @mention autocomplete.

GET /api/users          — list all verified users (id, name, email)
GET /api/users?q=alice  — filter by name/email prefix (case-insensitive)

Scoped behind the normal auth gate (require_token / Cloudflare Access).
Used by the Writing Studio comment composer @mention dropdown.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.identity.models import User
from artemis.identity.schemas import CurrentUserRead
from artemis.marketing.routes._auth import require_token

router = APIRouter(
    prefix="/api",
    tags=["identity"],
    dependencies=[Depends(require_token)],
)


def _serialize_user(user: User) -> CurrentUserRead:
    return CurrentUserRead(id=user.id, email=user.email, name=user.name)


@router.get("/users", response_model=list[CurrentUserRead])
async def list_users(
    q: str | None = Query(default=None, description="Filter by name/email prefix"),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[CurrentUserRead]:
    """Return verified users from the identity directory.

    Optionally filter by a case-insensitive ``q`` prefix matched against
    ``name`` and ``email``. Results are ordered by name asc, then email asc.
    Used for the @mention autocomplete dropdown in Writing Studio comments.
    """
    stmt = select(User).order_by(User.name.asc().nulls_last(), User.email.asc()).limit(limit)
    result = await session.execute(stmt)
    users = list(result.scalars().all())

    if q:
        needle = q.strip().lower()
        users = [u for u in users if needle in (u.name or "").lower() or needle in u.email.lower()]

    return [_serialize_user(u) for u in users]
