"""ORM models for proactive scheduled deliveries."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class MorningBriefDelivery(Base):
    """Once-per-day delivery reservation + outcome for the morning brief."""

    __tablename__ = "morning_brief_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "delivery_kind",
            "provider",
            "recipient_id",
            "delivery_date",
            name="uq_morning_brief_delivery_once_per_day",
        ),
        CheckConstraint(
            "status IN ('reserved', 'sent', 'failed')",
            name="ck_morning_brief_deliveries_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_kind: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    recipient_id: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="reserved")
    snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("brief_snapshots.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reserved_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
    delivered_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
