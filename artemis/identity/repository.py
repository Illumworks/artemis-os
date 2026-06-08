"""Persistence helpers for the identity users directory."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.identity.models import User


async def get_or_create_user(session: AsyncSession, email: str, name: str | None) -> User:
    """Upsert a users-directory row and bump its `last_seen_at`.

    The directory is keyed on normalized email and is lossless: repeated logins
    update recency and optional display name, but never create duplicates.
    """

    normalized_email = email.strip().lower()
    normalized_name = name.strip() if isinstance(name, str) and name.strip() else None

    upsert = insert(User).values(
        email=normalized_email,
        name=normalized_name,
    )
    upsert = upsert.on_conflict_do_update(
        index_elements=[User.email],
        set_={
            "name": func.coalesce(upsert.excluded.name, User.name),
            "last_seen_at": func.now(),
        },
    )
    await session.execute(upsert)

    result = await session.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one()
    return user
