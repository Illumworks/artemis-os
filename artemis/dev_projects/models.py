"""ORM models for Dev Projects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base


class DevProject(Base):
    __tablename__ = "dev_projects"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    last_opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default="{}"
    )

    sessions: Mapped[list[DevSession]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class DevSession(Base):
    __tablename__ = "dev_sessions"
    __table_args__ = (Index("ix_dev_sessions_project", "project_id", "last_active_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dev_projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="claude-code")
    model: Mapped[str | None] = mapped_column(Text)
    bypass_permissions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fork_of: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("dev_sessions.id", ondelete="SET NULL")
    )
    fork_at_message: Mapped[int | None] = mapped_column(BigInteger)
    forge_mode: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[DevProject] = relationship(back_populates="sessions")


class DevMessage(Base):
    __tablename__ = "dev_messages"
    __table_args__ = (Index("ix_dev_messages_session", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dev_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DevAnnotation(Base):
    __tablename__ = "dev_annotations"
    __table_args__ = (Index("ix_dev_annotations_session", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dev_sessions.id", ondelete="CASCADE"), nullable=False
    )
    url: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectWorkspaceMemory(Base):
    """One-brain drawer per project: plan, decisions log, file map, progress, open threads.

    Exactly one row per project (UNIQUE on project_id). The decisions column is
    append-only — entries are never removed (lossless memory rule).
    """

    __tablename__ = "project_workspace_memory"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_project_workspace_memory_project"),
        Index("ix_project_workspace_memory_project", "project_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dev_projects.id", ondelete="CASCADE"), nullable=False
    )
    # newest-wins free-text plan
    plan: Mapped[str | None] = mapped_column(Text)
    # append-only log: [{"ts": "<iso>", "text": "<str>"}, ...]
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    # newest-wins snapshot: {path: purpose}
    file_map: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    # newest-wins status text
    progress: Mapped[str | None] = mapped_column(Text)
    # unresolved items: [{"text": "<str>", ...}, ...]
    open_threads: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    project: Mapped[DevProject] = relationship()


class ForgeRun(Base):
    """One tracked server-side build job per Forge session.

    run_id is a stable string key (e.g. "run_<ulid>") used as the WS room key
    and as the FK target for ForgeRunLog so reconnecting clients can replay the
    log without needing the numeric PK.

    status is one of: running | completed | failed | cancelled.
    completed_at is NULL while the run is still active.
    """

    __tablename__ = "forge_runs"
    __table_args__ = (
        Index("ix_forge_runs_run_id", "run_id"),
        Index("ix_forge_runs_dev_session_id", "dev_session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    dev_session_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dev_sessions.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dev_projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="running")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ForgeRunLog(Base):
    """Append-only event log for a ForgeRun.

    seq is monotonically increasing per run (starting at 0).  It is assigned by
    the repository layer (max(seq)+1) rather than a DB sequence so that the
    caller controls ordering within a single transaction.

    kind is one of: token | message | tool_use | tool_result | permission |
                    error | status.

    This table is NEVER updated or deleted (lossless memory rule).
    """

    __tablename__ = "forge_run_log"
    __table_args__ = (Index("ix_forge_run_log_run_seq", "run_id", "seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("forge_runs.run_id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
