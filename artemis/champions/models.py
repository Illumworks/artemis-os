"""ORM models for the Champions digest.

One row per community item, which is the source every rollup derives from.
Hannah's version caches this in a file on Drive; rows in Postgres instead,
because the pod join wants a real join, the sheet is rebuilt from scratch
every run, and a cache file cannot be diffed against the previous week.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class ChampionsItem(Base):
    """A single discussion, comment or knowledge-base article.

    ``external_id`` is the dedupe key and mirrors the sheet's ``ID`` column:
    ``d-123`` / ``c-456`` / ``a-789``. Vanilla ids are only unique within a
    type, so the prefix is load-bearing, not decoration.
    """

    __tablename__ = "champions_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ── identity ──────────────────────────────────────────────────────────
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str] = mapped_column(Text, nullable=False)  # discussion|comment|article
    url: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    #: Vanilla's own post type -- discussion, tip, inspiration, newsletter and
    #: so on. Read from the source rather than inferred: the community already
    #: classifies every post this way, and the author chose it when they posted.
    post_type: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)

    # ── author ────────────────────────────────────────────────────────────
    # The body is stored because classification is re-runnable: the flagging
    # rules changed once already and will change again, and re-pulling 478
    # items to re-label them is worse than keeping 174k characters.
    #: For a comment, the discussion it belongs to. Needed for "Replied by
    #: Amira", which is a property of the THREAD, not of the item.
    parent_discussion_id: Mapped[int | None] = mapped_column(Integer)

    author_user_id: Mapped[int | None] = mapped_column(Integer)
    author_name: Mapped[str | None] = mapped_column(Text)
    author_email_domain: Mapped[str | None] = mapped_column(Text)
    is_amira_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    body: Mapped[str | None] = mapped_column(Text)

    # ── pod join ──────────────────────────────────────────────────────────
    # Null until the domain is resolved against the Cloudflare D1 pod
    # directory. Deliberately separate from ingestion: a domain that cannot be
    # placed is a row to review, not a row to drop, and resolution improves
    # over time as /pods/admin is corrected.
    district: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str | None] = mapped_column(Text)
    pod: Mapped[str | None] = mapped_column(Text)
    csm_email: Mapped[str | None] = mapped_column(Text)
    pod_resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    # ── classification ────────────────────────────────────────────────────
    summary: Mapped[str | None] = mapped_column(Text)
    theme: Mapped[str | None] = mapped_column(Text)
    product_issue: Mapped[bool | None] = mapped_column(Boolean)
    adoption_friction: Mapped[bool | None] = mapped_column(Boolean)
    escalation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escalation_terms: Mapped[list[str] | None] = mapped_column(JSONB)
    classified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    classifier_model: Mapped[str | None] = mapped_column(Text)

    replied_by_amira: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("external_id", name="uq_champions_items_external_id"),
        Index("idx_champions_items_posted_at", "posted_at"),
        Index("idx_champions_items_domain", "author_email_domain"),
        # Partial index for the classifier's work queue: unclassified rows only.
        Index(
            "idx_champions_items_unclassified",
            "posted_at",
            postgresql_where=classified_at.is_(None),
        ),
    )


class ChampionsRun(Base):
    """One row per ingest run — the sheet's ``Run log`` tab, and the first
    place to look when a run looks wrong."""

    __tablename__ = "champions_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")

    #: High-water mark this run read FROM, so a run is reproducible after the
    #: fact. Null on a full backfill.
    watermark: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_classified: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_flagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[str] | None] = mapped_column(JSONB)
