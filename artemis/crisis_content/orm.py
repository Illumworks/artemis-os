"""SQLAlchemy ORM models for the crisis-content approval pipeline (slice B1).

Three additive tables. See ``docs/crisis-content-approval-pipeline.md`` for
the design and ``alembic/versions/0106_crisis_content_transitions.py`` for
the migration that creates them -- alembic is this schema's authority, not
this module; nothing here calls ``Base.metadata.create_all()``.

``CrisisContentCard``   -- latest observed state, one row per card identity.
``CrisisContentCopyVersion`` -- append-only copy version log (CLAUDE.md
    rule 3, lossless memory). No code path in this package ever UPDATEs or
    DELETEs a row here.
``CrisisContentNotification`` -- the dedup ledger for "have we already
    notified on this (card, route, status)". Written only by
    ``artemis.crisis_content.transitions.mark_notified``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

__all__ = [
    "CrisisContentCard",
    "CrisisContentCopyVersion",
    "CrisisContentNotification",
]


class CrisisContentCard(Base):
    """Latest observed state for one review-card identity.

    Identity is ``(identity_header, identity_platform, identity_ordinal)``,
    mirroring ``ReviewCard.identity_key``. ``identity_platform`` is nullable
    (Jen's platform chip can be unset); uniqueness on the triple is enforced
    by a COALESCE-based expression index in the migration, not a plain
    ``UniqueConstraint`` here -- see that migration's docstring for why a
    plain constraint would let two platform-less rows collide.
    """

    __tablename__ = "crisis_content_cards"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    identity_header: Mapped[str] = mapped_column(Text, nullable=False)
    identity_platform: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    copy_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    copy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentCopyVersion(Base):
    """Append-only copy version log -- never UPDATEd, never DELETEd.

    One row per ``(card_id, copy_hash)`` first-seen. This is the only place
    the vendor's original wording survives a later edit -- see
    ``docs/crisis-content-approval-pipeline.md``, "Decided 2026-08-11 --
    capture edits and rejections, not just approvals", and ``CLAUDE.md``
    rule 3 (lossless memory). There is deliberately no public function
    anywhere in this package that updates or deletes a row in this table.
    """

    __tablename__ = "crisis_content_copy_versions"
    __table_args__ = (
        UniqueConstraint(
            "card_id", "copy_hash", name="uq_crisis_content_copy_versions_card_hash"
        ),
        Index("ix_crisis_content_copy_versions_card_id", "card_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("crisis_content_cards.id", name="fk_copy_versions_card", ondelete="CASCADE"),
        nullable=False,
    )
    copy_hash: Mapped[str] = mapped_column(Text, nullable=False)
    copy_body: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class CrisisContentNotification(Base):
    """Dedup ledger -- one row per ``(card, route, status)`` actually notified.

    Written by ``mark_notified`` (slice B2 calls it only after a successful
    Slack post -- never before, so a delivery failure is never mistaken for
    a delivered one). ``artemis.crisis_content.transitions`` otherwise only
    reads this table, via ``has_notified`` and the header-rename-guard join,
    to decide what to suppress.
    """

    __tablename__ = "crisis_content_notifications"
    __table_args__ = (
        UniqueConstraint(
            "card_id",
            "route",
            "status_value",
            name="uq_crisis_content_notifications_card_route_status",
        ),
        Index("ix_crisis_content_notifications_card_id", "card_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("crisis_content_cards.id", name="fk_notifications_card", ondelete="CASCADE"),
        nullable=False,
    )
    route: Mapped[str] = mapped_column(Text, nullable=False)
    status_value: Mapped[str] = mapped_column(Text, nullable=False)
    notified_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
