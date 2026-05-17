"""Async repository for the integrations domain.

Callers own commit/rollback. Raise ValueError for not-found conditions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations.models import Integration, SlackInboundMessage


async def upsert_integration(
    session: AsyncSession,
    *,
    provider: str,
    workspace_id: str,
    encrypted_credentials: bytes,
    display_name: str | None = None,
    bot_user_id: str | None = None,
    scopes: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Integration:
    """Insert or update an integration row keyed on (provider, workspace_id)."""
    values: dict[str, Any] = {
        "provider": provider,
        "workspace_id": workspace_id,
        "encrypted_credentials": encrypted_credentials,
        "display_name": display_name,
        "bot_user_id": bot_user_id,
        "scopes": scopes,
        "status": "active",
        "connected_at": datetime.now(UTC),
        "metadata": metadata or {},
    }
    stmt = (
        pg_insert(Integration)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["provider", "workspace_id"],
            set_={
                k: v
                for k, v in values.items()
                if k not in ("provider", "workspace_id", "connected_at")
            },
        )
        .returning(Integration)
    )
    result = await session.execute(stmt)
    row = result.scalar_one()
    return row


async def get_by_provider_and_workspace(
    session: AsyncSession,
    provider: str,
    workspace_id: str,
) -> Integration:
    result = await session.execute(
        select(Integration).where(
            Integration.provider == provider,
            Integration.workspace_id == workspace_id,
            Integration.status == "active",
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(
            f"No active integration for provider={provider!r} workspace={workspace_id!r}"
        )
    return row


async def get_by_id(session: AsyncSession, integration_id: int) -> Integration:
    result = await session.execute(select(Integration).where(Integration.id == integration_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Integration id={integration_id} not found")
    return row


async def list_active(session: AsyncSession, provider: str | None = None) -> list[Integration]:
    stmt = (
        select(Integration)
        .where(Integration.status == "active")
        .order_by(Integration.connected_at.desc())
    )
    if provider:
        stmt = stmt.where(Integration.provider == provider)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_revoked(session: AsyncSession, integration_id: int) -> Integration:
    result = await session.execute(
        update(Integration)
        .where(Integration.id == integration_id)
        .values(status="revoked")
        .returning(Integration)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Integration id={integration_id} not found")
    return row


async def mark_verified(session: AsyncSession, integration_id: int) -> None:
    await session.execute(
        update(Integration)
        .where(Integration.id == integration_id)
        .values(last_verified_at=datetime.now(UTC))
    )


async def upsert_slack_inbound(
    session: AsyncSession,
    *,
    event_id: str,
    team_id: str,
    channel_id: str,
    user_id: str,
    text: str | None,
    ts: str,
    thread_ts: str | None = None,
) -> bool:
    """Insert event; return True if newly inserted, False if duplicate."""
    stmt = (
        pg_insert(SlackInboundMessage)
        .values(
            event_id=event_id,
            team_id=team_id,
            channel_id=channel_id,
            user_id=user_id,
            text=text,
            ts=ts,
            thread_ts=thread_ts,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    result = await session.execute(stmt)
    return bool(getattr(result, "rowcount", 0))
