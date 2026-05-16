"""SQLAlchemy ORM models for the OKR Studio domain.

Tables:
  okr_objectives     — top-level quarterly objectives
  okr_key_results    — measurable key results under each objective
  okr_activity       — evidence / activity log entries
  okr_next_up        — prioritised action items (manual or agent-generated)
  okr_update_previews — ephemeral import diff previews

All timestamps are TIMESTAMPTZ. JSON-in-TEXT columns from Node are JSONB.
PKs are BIGSERIAL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base


class OkrObjective(Base):
    """Top-level quarterly / cycle objective."""

    __tablename__ = "okr_objectives"
    __table_args__ = (
        Index("idx_okr_objectives_cycle", "cycle"),
        Index("idx_okr_objectives_owner", "owner"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # Node uses 'desc' (reserved in PG)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tone: Mapped[str] = mapped_column(Text, nullable=False, server_default="sage")
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[str | None] = mapped_column(Text, nullable=True)
    cycle: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rolls_up_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    key_results: Mapped[list[OkrKeyResult]] = relationship(
        "OkrKeyResult",
        back_populates="objective",
        cascade="all, delete-orphan",
        order_by="OkrKeyResult.sort_order",
    )


class OkrKeyResult(Base):
    """Measurable key result belonging to an objective."""

    __tablename__ = "okr_key_results"
    __table_args__ = (Index("idx_okr_krs_objective", "objective_id", "sort_order"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    objective_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("okr_objectives.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    prog: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="notstarted")
    done_bullets: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    gaps_bullets: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    archive_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    objective: Mapped[OkrObjective] = relationship("OkrObjective", back_populates="key_results")
    activity: Mapped[list[OkrActivity]] = relationship(
        "OkrActivity",
        back_populates="key_result",
        foreign_keys="[OkrActivity.kr_id]",
    )


class OkrActivity(Base):
    """Evidence / activity log entry, optionally linked to a KR."""

    __tablename__ = "okr_activity"
    __table_args__ = (Index("idx_okr_activity_created", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    kr_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("okr_key_results.id", ondelete="SET NULL"),
        nullable=True,
    )
    kr_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    mapping_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    cleaned_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    key_result: Mapped[OkrKeyResult | None] = relationship(
        "OkrKeyResult", back_populates="activity", foreign_keys=[kr_id]
    )


class OkrNextUp(Base):
    """Prioritised action item — manual or agent-generated recommendation."""

    __tablename__ = "okr_next_up"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(Text, nullable=False, server_default="—")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    prio: Mapped[str] = mapped_column(Text, nullable=False, server_default="med")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    dismissed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    action_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="advice")
    dispatch_target: Mapped[str | None] = mapped_column(Text, nullable=True)
    dispatch_params: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)


class OkrUpdatePreview(Base):
    """Ephemeral import diff preview — committed_at is set when applied."""

    __tablename__ = "okr_update_previews"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    raw_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_format: Mapped[str] = mapped_column(Text, nullable=False, server_default="text")
    diff_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
