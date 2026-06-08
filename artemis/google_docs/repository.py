"""Persistence helpers for per-user Google Docs credentials."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.google_docs.models import GoogleCredential


async def get_google_credential(
    session: AsyncSession,
    *,
    user_id: int,
) -> GoogleCredential | None:
    result = await session.execute(
        select(GoogleCredential).where(GoogleCredential.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def upsert_google_credential(
    session: AsyncSession,
    *,
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    expiry: datetime,
    scope: str | None,
    connected_email: str | None,
) -> GoogleCredential:
    now = datetime.now(UTC)
    stmt = (
        insert(GoogleCredential)
        .values(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expiry=expiry,
            scope=scope,
            connected_email=connected_email,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[GoogleCredential.user_id],
            set_={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "expiry": expiry,
                "scope": scope,
                "connected_email": connected_email,
                "updated_at": now,
            },
        )
    )
    await session.execute(stmt)
    stored = await get_google_credential(session, user_id=user_id)
    if stored is None:
        raise RuntimeError("google credential upsert did not persist a row")
    return stored


async def delete_google_credential(
    session: AsyncSession,
    *,
    user_id: int,
) -> None:
    await session.execute(delete(GoogleCredential).where(GoogleCredential.user_id == user_id))
