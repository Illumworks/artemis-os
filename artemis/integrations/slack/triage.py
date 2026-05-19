"""Slack triage — mention queue + resolution.

Provides two coroutines consumed by the /api/slack/signals/mentions routes:
  list_unresolved_mentions(session, limit=20)  → list + total count
  resolve_mention(session, event_id)            → new total count (idempotent)
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations.models import SlackInboundMessage


def _make_permalink(channel_id: str, ts: str) -> str:
    """Build a Slack universal-format deep link for a message.

    https://slack.com/archives/<C123>/p<ts_no_dot>
    Works without knowing the workspace subdomain; Slack redirects to the correct
    workspace for any authenticated user.
    """
    ts_nodot = ts.replace(".", "")
    return f"https://slack.com/archives/{channel_id}/p{ts_nodot}"


async def list_unresolved_mentions(
    session: AsyncSession,
    limit: int = 20,
) -> dict[str, object]:
    """Return the most recent unresolved mentions.

    ``resolved_at IS NULL`` is the only filter — no time window, so old
    unresolved items stay visible until explicitly resolved.
    """
    rows_result = await session.execute(
        select(SlackInboundMessage)
        .where(SlackInboundMessage.resolved_at.is_(None))
        .order_by(SlackInboundMessage.ts.desc())
        .limit(limit)
    )
    rows = list(rows_result.scalars().all())

    count_result = await session.execute(
        select(func.count(SlackInboundMessage.event_id)).where(
            SlackInboundMessage.resolved_at.is_(None)
        )
    )
    total: int = count_result.scalar_one() or 0

    mentions = [
        {
            "id": row.event_id,
            "channel_id": row.channel_id,
            "channel_name": None,  # no slack_channels table yet; frontend uses channel_id
            "sender_user_id": row.user_id,
            "sender_name": None,  # no slack_users table yet; frontend uses sender_user_id
            "ts": row.ts,
            "text": row.text,
            "permalink": _make_permalink(row.channel_id, row.ts),
        }
        for row in rows
    ]

    return {"mentions": mentions, "total_unresolved": total}


async def resolve_mention(
    session: AsyncSession,
    event_id: str,
) -> tuple[bool, int]:
    """Set resolved_at = now() on a single row.

    Idempotent: if already resolved, no-op.
    Returns (found, new_total_unresolved).
    Raises ValueError when the event_id doesn't exist.
    """
    # Fetch first to validate existence
    row_result = await session.execute(
        select(SlackInboundMessage).where(SlackInboundMessage.event_id == event_id)
    )
    row = row_result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"No mention with event_id={event_id!r}")

    if row.resolved_at is None:
        await session.execute(
            update(SlackInboundMessage)
            .where(SlackInboundMessage.event_id == event_id)
            .values(resolved_at=datetime.now(UTC))
        )
        await session.commit()

    # Recount after potential update
    count_result = await session.execute(
        select(func.count(SlackInboundMessage.event_id)).where(
            SlackInboundMessage.resolved_at.is_(None)
        )
    )
    new_total: int = count_result.scalar_one() or 0
    return True, new_total
