"""SQLAlchemy ORM models for Floating Artemis.

Tables:
  floating_artemis_sessions     — one conversation context per open session
  floating_artemis_messages     — ordered message history with cost tracking
  floating_artemis_voice_corpus — characteristic phrase bank seeded from personality profile
  floating_artemis_page_context — current UI page context per session

Phase G1: data layer only. Chat orchestration and routes wire on top.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class FloatingArtemisSession(Base):
    """One persistent Floating Artemis conversation context."""

    __tablename__ = "floating_artemis_sessions"
    __table_args__ = (
        Index("idx_fa_sessions_owner", "owner_user_id"),
        Index("idx_fa_sessions_closed", "closed_at", postgresql_where="closed_at IS NULL"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_active_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[Any] = mapped_column("metadata", JSONB, nullable=False, server_default="'{}'")


class FloatingArtemisMessage(Base):
    """One message in a Floating Artemis session."""

    __tablename__ = "floating_artemis_messages"
    __table_args__ = (Index("idx_fa_messages_session_created", "session_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("floating_artemis_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[Any] = mapped_column(JSONB, nullable=False)
    cost_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cost_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cache_creation_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    cache_read_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class FloatingArtemisVoiceCorpus(Base):
    """Characteristic phrase bank seeded from personality profile."""

    __tablename__ = "floating_artemis_voice_corpus"
    __table_args__ = (Index("idx_fa_voice_corpus_active", "active", "source"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    line: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    context_tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_used_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    use_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="'seed'")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="TRUE")


class FloatingArtemisPageContext(Base):
    """Current UI page context for a session (latest row wins)."""

    __tablename__ = "floating_artemis_page_context"
    __table_args__ = (Index("idx_fa_page_context_session", "session_id", "set_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("floating_artemis_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    page: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    set_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
