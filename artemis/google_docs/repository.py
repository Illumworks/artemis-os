"""Persistence helpers for per-user Google credentials."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.google_docs.models import GoogleCredential

GooglePurpose = Literal["personal", "marketing"]


async def get_google_credential(
    session: AsyncSession,
    *,
    user_id: int,
    purpose: GooglePurpose = "personal",
) -> GoogleCredential | None:
    result = await session.execute(
        select(GoogleCredential).where(
            GoogleCredential.user_id == user_id,
            GoogleCredential.purpose == purpose,
        )
    )
    return result.scalar_one_or_none()


async def list_google_credentials(
    session: AsyncSession,
    *,
    user_id: int,
) -> list[GoogleCredential]:
    result = await session.execute(
        select(GoogleCredential)
        .where(GoogleCredential.user_id == user_id)
        .order_by(GoogleCredential.created_at.asc())
    )
    return list(result.scalars().all())


async def upsert_google_credential(
    session: AsyncSession,
    *,
    user_id: int,
    purpose: GooglePurpose,
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
            purpose=purpose,
            access_token=access_token,
            refresh_token=refresh_token,
            expiry=expiry,
            scope=scope,
            connected_email=connected_email,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=[GoogleCredential.user_id, GoogleCredential.purpose],
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
    stored = await get_google_credential(session, user_id=user_id, purpose=purpose)
    if stored is None:
        raise RuntimeError("google credential upsert did not persist a row")
    return stored


async def delete_google_credential(
    session: AsyncSession,
    *,
    user_id: int,
    purpose: GooglePurpose = "personal",
) -> None:
    await session.execute(
        delete(GoogleCredential).where(
            GoogleCredential.user_id == user_id,
            GoogleCredential.purpose == purpose,
        )
    )
