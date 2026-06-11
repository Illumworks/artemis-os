"""ORM models for proactive scheduled deliveries."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
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


class OkrCheckinBreadcrumb(Base):
    """Breadcrumb left when a Friday OKR check-in is posted.

    Exists for the TTL window (expires end of following Monday) so that
    handle_turn can detect a live check-in and inject OKR-reconcile context
    into the next DM turn, mapping Jon's word-dump to specific KRs and
    proposing update_okr_kr via the layer-3 confirm path.

    Lossless invariant: rows are never deleted. Expiry is via expires_at + completed_at.
    """

    __tablename__ = "okr_checkin_breadcrumbs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Slack user-ID of the check-in recipient (used to scope injection).
    recipient_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Serialised snapshot: list[{kr_id, kr_title, objective_title, prog, target_text}]
    kr_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # The full check-in text as delivered (for reference / audit).
    proposal_text: Mapped[str] = mapped_column(Text, nullable=False)
    # TTL: expires end of the following Monday so the window covers the whole weekend.
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    # Set when reconcile completes (layer-3 applied or declined), superseding the crumb.
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
