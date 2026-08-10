"""ORM models for meeting_summaries, meeting_match_log, and action-item dismissals."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class MeetingSummary(Base):
    """Idempotent, LLM-generated summary of a Granola meeting.

    UNIQUE on granola_id ensures scheduler re-runs are no-ops.
    raw_input_id links to the M1 hash-chain verbatim record.
    """

    __tablename__ = "meeting_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    granola_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    gcal_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # A JSON ARRAY of action-item dicts, not an object. The annotation said
    # `dict` for a long time while every write path stored a list
    # (`summarizer.py` builds `[item.model_dump() for item in ...]`), which is
    # why `proactivity/okr_checkin.py` grew an isinstance(list)/isinstance(dict)
    # branch to tolerate both. Verified against prod: all 37 non-null rows are
    # `jsonb_typeof = 'array'`, zero objects. The dict half of that branch is
    # dead code.
    action_items: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_input_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class MeetingActionItemDismissal(Base):
    """Lossless record that Jon dismissed a meeting action item as irrelevant.

    Keyed by (meeting_summary_id, action_item_key) where action_item_key is
    a SHA-256 hex hash of the normalised action-item text.  A present row
    means "skip this item forever — do NOT resurrect it on re-ingest or
    re-summarise."

    Rows are NEVER deleted (lossless invariant).
    """

    __tablename__ = "meeting_action_item_dismissals"
    __table_args__ = (
        UniqueConstraint(
            "meeting_summary_id",
            "action_item_key",
            name="uq_meeting_action_item_dismissals",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    meeting_summary_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("meeting_summaries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Stable content hash (SHA-256 hex) of the normalised text.
    action_item_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Granola meeting ID — denormalised for fast ingest-time lookup.
    granola_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Human-readable text (for audit trail).
    action_item_text: Mapped[str] = mapped_column(Text, nullable=False)
    dismissed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )


class MeetingMatchLog(Base):
    """Append-only log of title-match attempts (hits and misses).

    Never deleted; provides debugging visibility into GCal/Granola naming drift.

    outcome values:
      "matched_exact"      — exact title match
      "matched_fuzzy"      — case-insensitive substring match
      "matched_proximity"  — date-proximity tiebreak
      "no_match"           — no Granola meeting found within threshold
      "no_transcript"      — match found but Granola transcript not yet available
      "summarized"         — successfully summarized (success log line)
    """

    __tablename__ = "meeting_match_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    gcal_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    gcal_title: Mapped[str] = mapped_column(Text, nullable=False)
    gcal_end_time: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    matched_granola_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_candidate_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_candidate_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
