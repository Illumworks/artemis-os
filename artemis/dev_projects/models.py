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
    file_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
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
