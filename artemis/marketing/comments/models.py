"""ORM models for Writing Studio draft comments."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base
from artemis.identity.models import User
from artemis.marketing.models import CampaignDeliverable


class Comment(Base):
    """Lossless draft comment anchored to a Writing Studio deliverable."""

    __tablename__ = "comments"
    __table_args__ = (
        Index("idx_comments_draft_status", "draft_id", "status"),
        CheckConstraint("status IN ('open', 'resolved')", name="ck_comments_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("campaign_deliverables.id", name="fk_comments_draft", ondelete="CASCADE"),
        nullable=False,
    )
    author_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_comments_author_user", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("comments.id", name="fk_comments_parent", ondelete="CASCADE"),
        nullable=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anchor_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    anchored_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="open",
        server_default="open",
    )
    mentions: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", name="fk_comments_resolved_by_user", ondelete="SET NULL"),
        nullable=True,
    )

    draft: Mapped[CampaignDeliverable] = relationship("CampaignDeliverable", lazy="noload")
    author: Mapped[User] = relationship(
        "User",
        foreign_keys=[author_user_id],
        lazy="noload",
    )
    parent: Mapped[Comment | None] = relationship(
        "Comment",
        remote_side="Comment.id",
        foreign_keys=[parent_id],
        back_populates="replies",
        lazy="noload",
    )
    replies: Mapped[list[Comment]] = relationship(
        "Comment",
        back_populates="parent",
        lazy="noload",
    )
    resolved_by_user: Mapped[User | None] = relationship(
        "User",
        foreign_keys=[resolved_by_user_id],
        lazy="noload",
    )
