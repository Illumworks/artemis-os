"""SQLAlchemy 2.x async ORM models for the Automations domain (OP1).

Tables:
  automations      — registry of automation definitions
  automation_runs  — per-execution run records
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class Automation(Base):
    """Automation definition — holds target, schedule config, and approval policy."""

    __tablename__ = "automations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_automations_status",
        ),
        CheckConstraint(
            "trigger_type IN ('manual', 'scheduled', 'webhook')",
            name="ck_automations_trigger_type",
        ),
        CheckConstraint(
            "target_type IN ('agent', 'workflow', 'chain', 'dag')",
            name="ck_automations_target_type",
        ),
        Index("idx_automations_status_owner", "status", "owner_user_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    schedule_config: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_policy: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    output_config: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[Any | None] = mapped_column("metadata", JSONB, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class AutomationRun(Base):
    """Automation execution run — lifecycle: queued → running → succeeded|failed|cancelled."""

    __tablename__ = "automation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', 'cancelled')",
            name="ck_automation_runs_status",
        ),
        CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'webhook')",
            name="ck_automation_runs_trigger",
        ),
        Index("idx_automation_runs_automation_created", "automation_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    automation_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("automations.id", name="fk_automation_runs_automation", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    trigger: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    triggered_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    target_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Any | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
