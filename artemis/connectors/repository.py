"""Connector repository — all DB access for the connectors domain."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.connectors.encryption import decrypt_credentials, encrypt_credentials
from artemis.connectors.models import AgentConnector, Connector

logger = logging.getLogger(__name__)


# ── Connector CRUD ─────────────────────────────────────────────────────────────


async def list_connectors(
    session: AsyncSession,
    *,
    kind: str | None = None,
    status: str | None = None,
) -> list[Connector]:
    q = select(Connector)
    if kind is not None:
        q = q.where(Connector.kind == kind)
    if status is not None:
        q = q.where(Connector.status == status)
    q = q.order_by(Connector.created_at.desc())
    result = await session.execute(q)
    return list(result.scalars())


async def get_connector(session: AsyncSession, connector_id: str) -> Connector:
    row = (
        await session.execute(select(Connector).where(Connector.id == connector_id))
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"Connector not found: {connector_id!r}")
    return row


async def create_connector(
    session: AsyncSession,
    *,
    kind: str,
    name: str,
    credentials: dict[str, str],
    owner_user_id: int | None = None,
) -> Connector:
    blob = encrypt_credentials(credentials)
    row = Connector(
        kind=kind,
        name=name,
        credentials={"blob": blob},
        status="active",
        owner_user_id=owner_user_id,
        metadata_={},
    )
    session.add(row)
    await session.flush()
    return row


async def update_connector(
    session: AsyncSession,
    connector_id: str,
    *,
    name: str | None = None,
    credentials: dict[str, str] | None = None,
    status: str | None = None,
) -> Connector:
    row = await get_connector(session, connector_id)
    if name is not None:
        row.name = name
    if credentials is not None:
        blob = encrypt_credentials(credentials)
        row.credentials = {"blob": blob}
    if status is not None:
        row.status = status
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return row


async def soft_delete_connector(session: AsyncSession, connector_id: str) -> Connector:
    """Soft delete: set status → disabled."""
    return await update_connector(session, connector_id, status="disabled")


async def permanent_delete_connector(session: AsyncSession, connector_id: str) -> None:
    """Hard delete. Caller must verify connector is disabled first."""
    row = await get_connector(session, connector_id)
    if row.status != "disabled":
        raise ValueError("Permanent delete requires connector to be disabled first.")
    await session.execute(delete(Connector).where(Connector.id == connector_id))
    await session.flush()


async def mark_validated(session: AsyncSession, connector_id: str, *, success: bool) -> Connector:
    row = await get_connector(session, connector_id)
    meta: dict[str, Any] = dict(row.metadata_ or {})
    meta["last_validated_at"] = datetime.now(UTC).isoformat()
    meta["last_validation_ok"] = success
    row.metadata_ = meta
    row.updated_at = datetime.now(UTC)
    if not success and row.status == "active":
        row.status = "needs_reauth"
    elif success and row.status == "needs_reauth":
        row.status = "active"
    await session.flush()
    return row


def get_decrypted_credentials(row: Connector) -> dict[str, str]:
    """Decrypt credentials from a Connector row."""
    blob = (row.credentials or {}).get("blob", "")
    if not blob:
        return {}
    return decrypt_credentials(str(blob))


# ── Agent ↔ Connector linking ──────────────────────────────────────────────────


async def list_agent_connectors(session: AsyncSession, agent_id: int) -> list[AgentConnector]:
    result = await session.execute(
        select(AgentConnector).where(AgentConnector.agent_id == agent_id)
    )
    return list(result.scalars())


async def link_agent_connector(
    session: AsyncSession,
    *,
    agent_id: int,
    connector_id: str,
    tool_namespace: str,
) -> AgentConnector:
    stmt = (
        pg_insert(AgentConnector)
        .values(
            agent_id=agent_id,
            connector_id=connector_id,
            tool_namespace=tool_namespace,
        )
        .on_conflict_do_nothing(index_elements=["agent_id", "connector_id", "tool_namespace"])
        .returning(AgentConnector)
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        # Already existed — fetch it
        row = (
            await session.execute(
                select(AgentConnector).where(
                    AgentConnector.agent_id == agent_id,
                    AgentConnector.connector_id == connector_id,
                    AgentConnector.tool_namespace == tool_namespace,
                )
            )
        ).scalar_one()
    await session.flush()
    return row


async def unlink_agent_connector(
    session: AsyncSession,
    *,
    agent_id: int,
    connector_id: str,
) -> None:
    await session.execute(
        delete(AgentConnector).where(
            AgentConnector.agent_id == agent_id,
            AgentConnector.connector_id == connector_id,
        )
    )
    await session.flush()
