"""SQLAlchemy ORM models for Writing Studio rules + scaffolding.

Tables migrated (per Phase H spec):
  writing_profiles   — named writing identity / style guide container
  writing_folders    — hierarchical folder organiser for drafts
  writing_rules      — discrete voice / style rules
  writing_examples   — reference examples
  writing_sources    — imported reference documents

NOT here (per Phase H, explicitly excluded):
  writing_drafts, writing_draft_versions, writing_draft_thread_messages,
  writing_training_candidates, writing_deliverable_links, writing_draft_events

All timestamps are TIMESTAMPTZ. JSON-in-TEXT columns from Node are JSONB.
PKs are BIGSERIAL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base


class WritingProfile(Base):
    """Named writing style container — voice rules, examples, sources hang off this."""

    __tablename__ = "writing_profiles"
    __table_args__ = (Index("idx_writing_profiles_status", "status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    default_model_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_model_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    folders: Mapped[list[WritingFolder]] = relationship("WritingFolder", back_populates="profile")
    rules: Mapped[list[WritingRule]] = relationship("WritingRule", back_populates="profile")
    examples: Mapped[list[WritingExample]] = relationship(
        "WritingExample", back_populates="profile"
    )
    sources: Mapped[list[WritingSource]] = relationship("WritingSource", back_populates="profile")


class WritingFolder(Base):
    """Hierarchical folder for organising drafts within a profile."""

    __tablename__ = "writing_folders"
    __table_args__ = (
        Index("idx_writing_folders_profile", "profile_id"),
        Index("idx_writing_folders_campaign", "campaign_id"),
        Index("idx_writing_folders_parent", "parent_folder_id"),
        UniqueConstraint(
            "sync_id",
            name="idx_writing_folders_sync",
            # partial index emulated via deferrable — real partial done in migration
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sync_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id"), nullable=True
    )
    parent_folder_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("writing_folders.id", ondelete="SET NULL"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[WritingProfile | None] = relationship(
        "WritingProfile", back_populates="folders"
    )


class WritingRule(Base):
    """Discrete voice / style rule attached to a writing profile.

    Natural key: (profile_id, rule_type, title) where status != 'archived'.
    The unique index is partial; SQLAlchemy doesn't natively enforce partial
    uniqueness, so enforcement lives in the DB and in the repository layer.
    """

    __tablename__ = "writing_rules"
    __table_args__ = (
        Index("idx_writing_rules_profile", "profile_id"),
        Index("idx_writing_rules_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id"), nullable=True
    )
    rule_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="voice")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source_candidate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[WritingProfile | None] = relationship("WritingProfile", back_populates="rules")


class WritingExample(Base):
    """Reference example attached to a writing profile.

    Natural key: (profile_id, title, example_type).
    """

    __tablename__ = "writing_examples"
    __table_args__ = (
        Index("idx_writing_examples_profile", "profile_id"),
        Index("idx_writing_examples_type", "example_type"),
        UniqueConstraint(
            "profile_id",
            "title",
            "example_type",
            name="idx_writing_examples_profile_title_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    example_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="reference")
    asset_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_candidate_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[WritingProfile | None] = relationship(
        "WritingProfile", back_populates="examples"
    )


class WritingSource(Base):
    """Imported reference document attached to a writing profile.

    Natural key: (profile_id, source_key) — enforced by DB UNIQUE constraint.
    """

    __tablename__ = "writing_sources"
    __table_args__ = (
        Index("idx_writing_sources_profile", "profile_id"),
        Index("idx_writing_sources_type", "source_type"),
        UniqueConstraint("profile_id", "source_key", name="uq_writing_sources_profile_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id"), nullable=True
    )
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="reference")
    file_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_content: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[WritingProfile | None] = relationship(
        "WritingProfile", back_populates="sources"
    )
