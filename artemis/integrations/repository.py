"""Async repository for the integrations domain.

Callers own commit/rollback. Raise ValueError for not-found conditions.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations.models import Integration, IntegrationConfig, SlackInboundMessage


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
        # NOTE: use "metadata" (DB column name) not "metadata_" (ORM attr).
        # pg_insert must target Integration.__table__ to avoid SQLAlchemy
        # confusing "metadata" with Base.metadata when resolving ORM attrs.
        "metadata": metadata or {},
    }
    stmt = (
        pg_insert(Integration.__table__)  # type: ignore[arg-type]
        .values(**values)
        .on_conflict_do_update(
            index_elements=["provider", "workspace_id"],
            set_={
                k: v
                for k, v in values.items()
                if k not in ("provider", "workspace_id", "connected_at")
            },
        )
        .returning(Integration.__table__.c.id)
    )
    result = await session.execute(stmt)
    row_id: int = result.scalar_one()
    # Re-fetch as ORM object so callers receive a fully-mapped Integration.
    orm_result = await session.execute(select(Integration).where(Integration.id == row_id))
    return orm_result.scalar_one()


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


# ── Provider config (J1b) ────────────────────────────────────────────────────


async def upsert_provider_config(
    session: AsyncSession,
    provider: str,
    payload_dict: dict[str, object],
) -> None:
    """Merge payload_dict into existing config for provider, encrypt, upsert.

    Only non-empty values from payload_dict overwrite existing keys; blank
    values are treated as "leave as is" so partial updates are safe.
    """
    from artemis.integrations.crypto import encrypt_credentials

    existing: dict[str, object] = {}
    with contextlib.suppress(Exception):
        existing = await get_provider_config(session, provider) or {}

    updates = {k: v for k, v in payload_dict.items() if v and str(v).strip()}
    merged = {**existing, **updates}
    encrypted = encrypt_credentials(merged)

    stmt = (
        pg_insert(IntegrationConfig)
        .values(
            provider=provider,
            encrypted_payload=encrypted,
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_update(
            index_elements=["provider"],
            set_={
                "encrypted_payload": encrypted,
                "updated_at": datetime.now(UTC),
            },
        )
    )
    await session.execute(stmt)


async def get_provider_config(
    session: AsyncSession,
    provider: str,
) -> dict[str, object] | None:
    """Return decrypted credential dict for provider, or None if not stored."""
    from artemis.integrations.crypto import decrypt_credentials

    result = await session.execute(
        select(IntegrationConfig).where(IntegrationConfig.provider == provider)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    try:
        return decrypt_credentials(bytes(row.encrypted_payload))
    except Exception:
        return None


async def get_provider_config_status(
    session: AsyncSession,
    provider: str,
) -> dict[str, bool]:
    """Return {key: True/False} for each stored key (boolean only, never values).

    Returns an empty dict if no config row exists for provider.
    """
    config = await get_provider_config(session, provider)
    if config is None:
        return {}
    return {k: bool(v and str(v).strip()) for k, v in config.items()}


async def delete_provider_config(
    session: AsyncSession,
    provider: str,
) -> None:
    """Remove all stored credentials for provider."""
    from sqlalchemy import delete as sa_delete

    await session.execute(
        sa_delete(IntegrationConfig).where(IntegrationConfig.provider == provider)
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
