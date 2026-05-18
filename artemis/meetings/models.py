"""ORM models for meeting_summaries and meeting_match_log."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Integer, Text
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
    action_items: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    raw_input_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


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
