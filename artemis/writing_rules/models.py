"""SQLAlchemy ORM models for Writing Studio rules + scaffolding.

Tables migrated (per Phase H spec):
  writing_profiles   — named writing identity / style guide container
  writing_folders    — hierarchical folder organiser for drafts
  writing_rules      — discrete voice / style rules
  writing_examples   — reference examples
  writing_sources    — imported reference documents

Phase 2 substrate (ws-thread-messages, migration 0063):
  writing_draft_thread_messages — per-draft AI conversation thread

Phase 3 Piece B (migration 0064):
  writing_training_candidates — proposed / approved / rejected learning candidates

NOT here (per Phase H, explicitly excluded):
  writing_drafts, writing_draft_versions,
  writing_deliverable_links, writing_draft_events

All timestamps are TIMESTAMPTZ. JSON-in-TEXT columns from Node are JSONB.
PKs are BIGSERIAL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    claims: Mapped[list[Claim]] = relationship("Claim", back_populates="profile")
    templates: Mapped[list[Template]] = relationship("Template", back_populates="profile")
    training_candidates: Mapped[list[WritingTrainingCandidate]] = relationship(
        "WritingTrainingCandidate", back_populates="profile"
    )


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
    # Soft-delete tombstone for campaign-derived folders.
    # Stamped when the user explicitly deletes a campaign folder so that
    # backfill_campaign_folders does not recreate it on the next overview load.
    # User-created folders (campaign_id IS NULL) are hard-deleted; for them
    # this column will never be set.
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

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
    tag_scope: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
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


class Claim(Base):
    """Structured claims-register row tied to a writing profile."""

    __tablename__ = "claims"
    __table_args__ = (
        Index("idx_claims_profile_status", "profile_id", "status"),
        UniqueConstraint("profile_id", "claim_code", name="uq_claims_profile_code"),
        CheckConstraint("tier IS NULL OR tier BETWEEN 1 AND 4", name="ck_claims_tier_range"),
        CheckConstraint(
            "status IN ('proposed', 'approved', 'retired')",
            name="ck_claims_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id"), nullable=False
    )
    claim_code: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_phrasing: Mapped[str] = mapped_column(Text, nullable=False)
    packaging: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="approved")
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("claims.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[WritingProfile] = relationship("WritingProfile", back_populates="claims")
    superseded_claim: Mapped[Claim | None] = relationship("Claim", remote_side="Claim.id")


class Template(Base):
    """Structured copy-ready template tied to a writing profile."""

    __tablename__ = "templates"
    __table_args__ = (
        Index("idx_templates_profile_status", "profile_id", "status"),
        UniqueConstraint("profile_id", "template_key", name="uq_templates_profile_key"),
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="ck_templates_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id"), nullable=False
    )
    template_key: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    asset_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    profile: Mapped[WritingProfile] = relationship("WritingProfile", back_populates="templates")
    superseded_template: Mapped[Template | None] = relationship(
        "Template", remote_side="Template.id"
    )


class TagDimension(Base):
    """Registry dimension used to tag Writing Studio assets and rules."""

    __tablename__ = "tag_dimensions"
    __table_args__ = (Index("idx_tag_dimensions_active_sort", "active", "sort_order", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    values: Mapped[list[TagValue]] = relationship(
        "TagValue",
        back_populates="dimension",
        primaryjoin="TagDimension.key == foreign(TagValue.dimension_key)",
    )


class TagValue(Base):
    """Registry value within a dimension, optionally scoped under a parent value."""

    __tablename__ = "tag_values"
    __table_args__ = (
        Index(
            "idx_tag_values_dimension_active_sort", "dimension_key", "active", "sort_order", "id"
        ),
        Index("idx_tag_values_parent_lookup", "dimension_key", "parent_value", "sort_order", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dimension_key: Mapped[str] = mapped_column(
        Text,
        ForeignKey("tag_dimensions.key", ondelete="RESTRICT"),
        nullable=False,
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    parent_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("TRUE"),
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    value_metadata: Mapped[Any] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    dimension: Mapped[TagDimension] = relationship(
        "TagDimension",
        back_populates="values",
        primaryjoin="foreign(TagValue.dimension_key) == TagDimension.key",
    )


class WritingDraftThreadMessage(Base):
    """One turn in the per-draft AI conversation thread.

    Mirrors the Node ``writing_draft_thread_messages`` table in db/sqlite.js,
    with these adaptations for Postgres / the Python rebuild:

    - ``draft_id`` FKs to ``campaign_deliverables.id`` (Python rebuild's draft
      row) rather than the Node's ``writing_drafts.id``. The Python app stores
      draft state on ``campaign_deliverables``; there is no ``writing_drafts``
      table yet.
    - ``created_at`` is TIMESTAMPTZ (not a Unix integer as in SQLite).
    - Node's ``*_json`` TEXT columns become JSONB:
        - ``attachments_json`` → ``attachments``
        - ``trace_json``       → ``trace``
        - ``engine_json``      → ``engine``
        - ``prompt_json``      → ``prompt``
      The JSONB rename drops the ``_json`` suffix because Postgres can query
      them natively; the compose endpoint (Phase 2 piece ②) uses the same
      names when reading rows back.

    Node columns carried over without change: ``role``, ``label``, ``text``.

    Column-purpose notes (ambiguity from the Node reference):
    - ``label``:   human-readable sender label (e.g. "System", "Artemis"),
                   nullable — Node callers set it but it's display-only.
    - ``trace``:   JSONB snapshot of rules/examples/draft context used in the
                   generation turn; written on assistant messages only.
    - ``engine``:  JSONB: {providerId, modelId, resolvedModelId, sessionId};
                   written on assistant messages only.
    - ``prompt``:  JSONB: {systemPrompt, userPrompt}; written on assistant
                   messages only for audit/debugging. May be large.
    - ``attachments``: JSONB array of attachment excerpts passed with the
                   user message; written on user messages only.
    """

    __tablename__ = "writing_draft_thread_messages"
    __table_args__ = (
        Index(
            "idx_writing_thread_messages_draft",
            "draft_id",
            "created_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("campaign_deliverables.id", name="fk_wdtm_draft", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Node column name is ``text``; keep it for semantic parity.
    content: Mapped[str] = mapped_column("text", Text, nullable=False)
    attachments: Mapped[Any | None] = mapped_column("attachments_json", JSONB, nullable=True)
    trace: Mapped[Any | None] = mapped_column("trace_json", JSONB, nullable=True)
    engine: Mapped[Any | None] = mapped_column("engine_json", JSONB, nullable=True)
    prompt: Mapped[Any | None] = mapped_column("prompt_json", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class WritingTrainingCandidate(Base):
    """Proposed learning candidate from a Writing Studio compose turn.

    A candidate is created when the AI detects a "Proposed learning: ..." line
    in its response. A human reviewer then approves (→ promotes to WritingRule
    or WritingExample) or rejects it. Rejected candidates are never deleted
    (lossless memory rule); they simply carry status='rejected' + decided_at.

    Node reference: db/sqlite.js writing_training_candidates (lines 616-632).
    Adaptations:
      - draft_id FKs to campaign_deliverables.id (Python draft row).
      - scope_json is JSONB (not TEXT as in SQLite).
    """

    __tablename__ = "writing_training_candidates"
    __table_args__ = (
        Index("idx_writing_training_candidates_profile", "profile_id"),
        Index("idx_writing_training_candidates_status", "status"),
        Index("idx_writing_training_candidates_draft", "draft_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    profile_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("writing_profiles.id", name="fk_wtc_profile"), nullable=True
    )
    draft_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("campaign_deliverables.id", name="fk_wtc_draft", ondelete="SET NULL"),
        nullable=True,
    )
    candidate_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="rule")
    proposed_text: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="proposed")
    scope_json: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    source_version_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    approved_memory_observation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    profile: Mapped[WritingProfile | None] = relationship(
        "WritingProfile", back_populates="training_candidates"
    )
