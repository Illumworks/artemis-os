"""ORM models for cached Google Calendar events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class GCalEventCache(Base):
    """Read-through cache of recent/future Google Calendar events."""

    __tablename__ = "gcal_events_cache"
    __table_args__ = (
        UniqueConstraint("calendar_id", "event_id", name="uq_gcal_events_cache_calendar_event"),
        Index("idx_gcal_events_cache_range", "start_at", "end_at"),
        Index("idx_gcal_events_cache_calendar", "calendar_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    calendar_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    attendees: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
