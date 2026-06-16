"""Repository helpers for the hub escalation layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.hub.models import AgentPendingAsk

# Default wait window before Artemis escalates (≈1 day).
_DEFAULT_ESCALATION_WINDOW = timedelta(hours=24)


async def record_pending_ask(
    session: AsyncSession,
    *,
    agent_id: str,
    channel_id: str,
    message_ts: str,
    summary: str,
) -> tuple[AgentPendingAsk, bool]:
    """Insert a new pending ask or return the existing row.

    Returns ``(row, created)`` where ``created=True`` on first insert.
    Idempotent on ``(agent_id, channel_id, message_ts)`` — safe to call on
    every outbound agent message.
    """
    now = datetime.now(UTC)
    stmt = (
        pg_insert(AgentPendingAsk)
        .values(
            agent_id=agent_id,
            channel_id=channel_id,
            message_ts=message_ts,
            summary=summary[:500],
            created_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_agent_pending_asks_key")
        .returning(AgentPendingAsk.id)
    )
    inserted_id = (await session.execute(stmt)).scalar_one_or_none()
    if inserted_id is not None:
        row = await session.get(AgentPendingAsk, inserted_id)
        assert row is not None
        return row, True

    result = await session.execute(
        select(AgentPendingAsk).where(
            AgentPendingAsk.agent_id == agent_id,
            AgentPendingAsk.channel_id == channel_id,
            AgentPendingAsk.message_ts == message_ts,
        )
    )
    row = result.scalar_one()
    return row, False


async def resolve_pending_asks_in_channel(
    session: AsyncSession,
    *,
    channel_id: str,
    resolved_at: datetime | None = None,
) -> int:
    """Mark all unresolved pending asks in a channel as resolved.

    Called when Jon posts in a channel — any unresolved ask in that channel
    (or its thread) is considered answered.

    Returns the number of rows updated.
    """
    ts = resolved_at or datetime.now(UTC)
    result = await session.execute(
        update(AgentPendingAsk)
        .where(
            AgentPendingAsk.channel_id == channel_id,
            AgentPendingAsk.resolved_at.is_(None),
        )
        .values(resolved_at=ts)
        .returning(AgentPendingAsk.id)
    )
    rows = result.fetchall()
    return len(rows)


async def list_overdue_unescalated(
    session: AsyncSession,
    *,
    window: timedelta = _DEFAULT_ESCALATION_WINDOW,
    now: datetime | None = None,
) -> list[AgentPendingAsk]:
    """Return pending asks that are overdue and not yet escalated.

    An ask is "overdue" when:
    - ``resolved_at IS NULL`` (Jon hasn't answered)
    - ``escalated_at IS NULL`` (we haven't already escalated this one)
    - ``created_at < now - window`` (the wait window has elapsed)
    """
    cutoff = (now or datetime.now(UTC)) - window
    result = await session.execute(
        select(AgentPendingAsk)
        .where(
            AgentPendingAsk.resolved_at.is_(None),
            AgentPendingAsk.escalated_at.is_(None),
            AgentPendingAsk.created_at < cutoff,
        )
        .order_by(AgentPendingAsk.created_at.asc())
    )
    return list(result.scalars().all())


async def list_unresolved(
    session: AsyncSession,
) -> list[AgentPendingAsk]:
    """Return all pending asks that are still unresolved (for brief injection)."""
    result = await session.execute(
        select(AgentPendingAsk)
        .where(AgentPendingAsk.resolved_at.is_(None))
        .order_by(AgentPendingAsk.created_at.asc())
    )
    return list(result.scalars().all())


async def mark_escalated(
    session: AsyncSession,
    *,
    ask_id: int,
    escalated_at: datetime | None = None,
) -> None:
    """Stamp the escalated_at timestamp so the sweep doesn't fire again."""
    ts = escalated_at or datetime.now(UTC)
    await session.execute(
        update(AgentPendingAsk)
        .where(AgentPendingAsk.id == ask_id)
        .values(escalated_at=ts)
    )
