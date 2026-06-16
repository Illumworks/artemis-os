"""ORM model for agent pending asks.

A pending ask is created when a named agent (Kai, Callie, etc.) posts a
message that @-mentions Jon or asks him a direct question.  It is resolved
when Jon replies in the same channel/thread.

Rows are lossless — never deleted.  Resolution is via ``resolved_at != NULL``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class AgentPendingAsk(Base):
    """Tracks an unanswered ask from a named agent directed at Jon.

    Unique key: ``(agent_id, channel_id, message_ts)`` — one row per message.
    The escalation sweep queries rows where ``resolved_at IS NULL`` and
    ``created_at < now() - 1 day``.
    """

    __tablename__ = "agent_pending_asks"
    __table_args__ = (
        UniqueConstraint(
            "agent_id",
            "channel_id",
            "message_ts",
            name="uq_agent_pending_asks_key",
        ),
        # Fast scan for overdue unresolved asks.
        Index("idx_agent_pending_asks_unresolved", "resolved_at", "created_at"),
        # Fast lookup by channel for the reply-resolution hook.
        Index("idx_agent_pending_asks_channel", "channel_id", "resolved_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # The named agent that posted the ask ("kai", "callie", …).
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Slack channel where the ask was posted.
    channel_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Slack message timestamp of the agent's ask (the ``ts`` field).
    message_ts: Mapped[str] = mapped_column(Text, nullable=False)

    # Short human-readable summary of what the agent asked (for escalation text).
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # When the pending ask was first recorded.
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )

    # Set when Jon replies — NULL means unresolved.
    resolved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Set when Artemis has already posted the escalation comment for this ask,
    # to prevent duplicate escalation posts.
    escalated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
