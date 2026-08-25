"""Persistence for file readings.

**We store the reading, not the file.** Bytes are fetched, extracted and
dropped; Slack and Drive keep the original and remain the system of record, and
`source_url` points back at it. Storage therefore grows with text people
actually used rather than with every binary dropped into a channel -- and when
someone deletes a file at the source, we are not sitting on a copy of it.

**This table is a cache, and the lossless-memory rule does not reach it.**
CLAUDE.md forbids deleting drawers and observations; that protects *evidence*.
An extraction is a convenience so the same file is not re-fetched and re-parsed
on every turn. The rule is respected by PROMOTION: when an agent uses a file's
content in an observation, signal or brief, the excerpt is copied into the
memory system, where it is permanent. `promoted_at` records that this happened,
and a promoted row is exempt from expiry so the trail back to the source
survives even after the cache would ordinarily have let it go.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base

# How long an unpromoted extraction stays useful. A file's readable value is
# overwhelmingly conversational -- someone asks about it in the minutes or days
# after it is shared. Anything that mattered longer than that should have been
# promoted into memory, which is the durable store.
EXTRACTION_TTL = timedelta(days=90)


class FileExtraction(Base):
    """One file, as read once. Never the file itself."""

    __tablename__ = "file_extractions"
    __table_args__ = (
        # A file is identified by its source and that source's own id, so the
        # same Slack upload referenced from several threads is stored once.
        UniqueConstraint("source", "source_id", name="uq_file_extractions_source"),
        Index("ix_file_extractions_expiry", "promoted_at", "created_at"),
        Index("ix_file_extractions_channel", "channel_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, default="", nullable=False)

    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mimetype: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="unknown", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # The extraction itself. NULL when the read failed -- a failure is recorded
    # so the same unreadable file is not re-fetched on every single turn, and so
    # "why did it not read this" is answerable after the fact.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    tables: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB, nullable=True)
    notes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    truncated: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Set together: the failure class and the sentence an agent may repeat.
    failure_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Where it came from, for attribution and for scoping a later search.
    channel_id: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    shared_by: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    message_ts: Mapped[str] = mapped_column(String(32), default="", nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_read_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    # Non-NULL once the content became evidence somewhere durable. Exempts the
    # row from expiry.
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Whether this row may be purged. Promoted rows never expire."""
        if self.promoted_at is not None:
            return False
        reference = now or datetime.now(UTC)
        return (reference - self.last_read_at) > EXTRACTION_TTL
