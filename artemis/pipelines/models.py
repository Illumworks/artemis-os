"""SQLAlchemy 2.x async ORM models for the Pipelines domain (PIPE1).

Tables:
  pipelines      — unified orchestration primitive (nodes + edges JSONB)
  pipeline_runs  — per-execution run records
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class Pipeline(Base):
    """Pipeline definition — holds nodes, edges, and trigger config as JSONB."""

    __tablename__ = "pipelines"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_pipelines_status",
        ),
        Index("idx_pipelines_status_owner", "status", "owner_user_id"),
        Index("idx_pipelines_nodes_gin", "nodes", postgresql_using="gin"),
        Index("idx_pipelines_edges_gin", "edges", postgresql_using="gin"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    nodes: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    edges: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    trigger_config: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    metadata_: Mapped[Any | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class PipelineRun(Base):
    """Pipeline execution run — lifecycle: queued → running → succeeded|failed|cancelled."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', 'cancelled')",
            name="ck_pipeline_runs_status",
        ),
        CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'webhook', 'event')",
            name="ck_pipeline_runs_trigger",
        ),
        Index("idx_pipeline_runs_pipeline_started", "pipeline_id", "started_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    pipeline_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("pipelines.id", name="fk_pipeline_runs_pipeline", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    trigger: Mapped[str] = mapped_column(Text, nullable=False, server_default="manual")
    triggered_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    node_states: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Any | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
