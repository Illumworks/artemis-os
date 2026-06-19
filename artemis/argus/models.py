"""ORM model for persisting Argus research dispatch requests.

argus_research_requests tracks every ``dispatch_research`` call so that a
process restart mid-dig can recover and complete the work.

Lifecycle:
    pending  → task created, background work not yet done
    done     → research + Slack post completed successfully
    failed   → attempts >= 3 (hard cap), fallback Slack message posted

The recovery hook (app startup) queries ``status='pending' AND attempts < 3``
and re-fires ``_research_and_post`` for each orphaned row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class ArgusResearchRequest(Base):
    """Persistent record for a single Argus dispatch invocation."""

    __tablename__ = "argus_research_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    district_key: Mapped[str] = mapped_column(Text, nullable=False)

    # Resolved at dispatch time from the session context.
    channel_id: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # Optional triggering context.
    signal: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    triggering_signal_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="pending",
        # valid values: 'pending' | 'done' | 'failed'
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
