"""SQLAlchemy 2.x async ORM models for the memory keystone.

Core tables:
  memory_scopes             — scope catalog (UX helper, not query-critical)
  memory_drawers            — verbatim layer; immutable evidence floor
  memory_observations       — curated layer; what retrieval reads at prompt time
  memory_evidence           — many-to-many link: drawer/obs → observation

Graph layer (B4):
  memory_entities           — named entities extracted from observations
  memory_entity_aliases     — surface-form aliases for entities
  memory_entity_mentions    — links entity to the obs/drawer that mentioned it
  memory_relations          — directed predicate-labelled edges between entities
  memory_relation_rejections — dev-only log of rejected unknown predicates

LOSSLESS RULE: drawers and observations are never deleted from the DB.
Observations leave active retrieval only via superseded_by, never DELETE.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector as _PgVector  # type: ignore[import-untyped]
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from artemis.db import Base


class Vector(_PgVector):  # type: ignore[misc]
    """`pgvector.sqlalchemy.Vector` with asyncpg-compatible parameter binding.

    pgvector's stock SQLAlchemy type calls `Vector._to_db()` in its
    `bind_processor`, which serializes the list to a text form like
    `'[0.1, 0.2, ...]'`. That works for psycopg2 but breaks under asyncpg,
    which receives a text string and tries to bind it as a float (because
    the registered asyncpg codec uses binary format and expects a list /
    ndarray, not pre-serialized text).

    On asyncpg dialect we return None from `bind_processor`, telling
    SQLAlchemy to pass the value through unchanged. The asyncpg codec
    (registered in `artemis.db.attach_pgvector_codec`) then encodes the
    list / ndarray to vector binary format.

    Reference: pgvector-python issue surface area — the stock type assumes
    psycopg; asyncpg needs the bind processor bypassed.
    """

    def bind_processor(self, dialect):  # type: ignore[no-untyped-def]
        if dialect.driver == "asyncpg":
            return None
        return super().bind_processor(dialect)


class MemoryScope(Base):
    __tablename__ = "memory_scopes"
    __table_args__ = (Index("idx_memory_scopes_parent", "parent_scope_kind", "parent_scope_id"),)

    scope_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_scope_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_scope_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class MemoryDrawer(Base):
    """Verbatim layer. Written once; content never changes. Serves as the evidence
    floor for observations. The unique constraint on (scope_kind, scope_id,
    content_hash) means identical content in the same scope deduplicates on write."""

    __tablename__ = "memory_drawers"
    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_id", "content_hash", name="uq_drawers_scope_hash"),
        Index("idx_memory_drawers_scope", "scope_kind", "scope_id"),
        Index("idx_memory_drawers_source", "source_kind", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    corpus_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_extra: Mapped[Any] = mapped_column(JSONB, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Generated column — maintained by Postgres; never written by ORM.
    content_fts: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
        deferred=True,
    )


class MemoryObservation(Base):
    """Curated layer. Retrieved at prompt-build time. Consolidated from drawers.
    Retired via superseded_by — never deleted. The partial index on active
    (superseded_by IS NULL) keeps active-only queries fast."""

    __tablename__ = "memory_observations"
    __table_args__ = (
        UniqueConstraint("scope_kind", "scope_id", "content_hash", name="uq_obs_scope_hash"),
        Index("idx_memory_observations_scope", "scope_kind", "scope_id"),
        Index("idx_memory_observations_category", "category"),
        Index("idx_memory_observations_score", "score"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default="discovery")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    source_quality: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    user_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    valid_from: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memory_observations.id", name="fk_obs_superseded_by"),
        nullable=True,
    )
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    accessed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    # Generated column — maintained by Postgres; never written by ORM.
    content_fts: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('english', content)", persisted=True),
        nullable=True,
        deferred=True,
    )
    # Graph extraction tracking (B4 additive columns)
    graph_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    graph_last_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    evidence: Mapped[list[MemoryEvidence]] = relationship(
        "MemoryEvidence",
        back_populates="observation",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class MemoryEvidence(Base):
    """Many-to-many link from a drawer or observation to the observation it supports.
    Uniqueness on (observation_id, source_kind, source_id) makes linking idempotent."""

    __tablename__ = "memory_evidence"
    __table_args__ = (
        UniqueConstraint(
            "observation_id", "source_kind", "source_id", name="uq_evidence_obs_source"
        ),
        Index("idx_memory_evidence_observation", "observation_id"),
        Index("idx_memory_evidence_source", "source_kind", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "memory_observations.id",
            ondelete="CASCADE",
            name="fk_evidence_observation",
        ),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    observation: Mapped[MemoryObservation] = relationship(
        "MemoryObservation",
        back_populates="evidence",
        lazy="noload",
    )


class MemoryEmbedding(Base):
    """Stores embedding vectors for drawers and observations.

    One row per (target_table, target_id, model_version). The HNSW index on
    embedding enables fast approximate cosine similarity search via pgvector.
    Embedding writes are best-effort — absence means "needs backfill", not error.
    """

    __tablename__ = "memory_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "target_table",
            "target_id",
            "model_version",
            name="uq_embeddings_target_model",
        ),
        Index("idx_memory_embeddings_target", "target_table", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    target_table: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(384), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


# ── Graph layer (B4) ─────────────────────────────────────────────────────────


class MemoryEntity(Base):
    """Named entity extracted from observations.

    Unique per (scope_kind, scope_id, entity_kind, name_slug). Upsert bumps
    mention_count + last_seen_at rather than duplicating. Entities are scope-local
    by design — the same name in two scopes produces two rows.
    """

    __tablename__ = "memory_entities"
    __table_args__ = (
        UniqueConstraint(
            "scope_kind",
            "scope_id",
            "entity_kind",
            "name_slug",
            name="uq_entities_scope_kind_slug",
        ),
        Index("idx_entities_scope", "scope_kind", "scope_id"),
        Index("idx_entities_kind_slug", "entity_kind", "name_slug"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_slug: Mapped[str] = mapped_column(Text, nullable=False)
    scope_kind: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[Any] = mapped_column(JSONB, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.9")
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memory_entities.id", name="fk_entity_superseded_by"),
        nullable=True,
    )

    aliases: Mapped[list[MemoryEntityAlias]] = relationship(
        "MemoryEntityAlias",
        back_populates="entity",
        cascade="all, delete-orphan",
        lazy="noload",
    )
    mentions: Mapped[list[MemoryEntityMention]] = relationship(
        "MemoryEntityMention",
        back_populates="entity",
        cascade="all, delete-orphan",
        lazy="noload",
    )


class MemoryEntityAlias(Base):
    """Surface-form aliases for an entity (e.g. "Jon" for "Jonathan").

    alias_slug is the normalized form used for text matching. Unique per
    (entity_id, alias_slug) so the same alias can't be recorded twice.
    """

    __tablename__ = "memory_entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias_slug", name="uq_aliases_entity_slug"),
        Index("idx_aliases_slug", "alias_slug"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_entities.id", ondelete="CASCADE", name="fk_alias_entity"),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(Text, nullable=False)
    alias_slug: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[MemoryEntity] = relationship(
        "MemoryEntity", back_populates="aliases", lazy="noload"
    )


class MemoryEntityMention(Base):
    """Link between an entity and the observation/drawer that mentioned it.

    Unique per (entity_id, source_kind, source_id) — idempotent on re-extraction.
    """

    __tablename__ = "memory_entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "entity_id",
            "source_kind",
            "source_id",
            name="uq_mentions_entity_source",
        ),
        Index("idx_mentions_source", "source_kind", "source_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_entities.id", ondelete="CASCADE", name="fk_mention_entity"),
        nullable=False,
    )
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mention_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    entity: Mapped[MemoryEntity] = relationship(
        "MemoryEntity", back_populates="mentions", lazy="noload"
    )


class MemoryRelation(Base):
    """Directed predicate-labelled edge between two entities.

    Subject + predicate + object are unique — re-extraction bumps last_seen_at
    rather than duplicating. evidence_observation_id tracks provenance.
    """

    __tablename__ = "memory_relations"
    __table_args__ = (
        UniqueConstraint("subject_id", "predicate", "object_id", name="uq_relations_triple"),
        Index("idx_rel_subject", "subject_id", "predicate"),
        Index("idx_rel_object", "object_id", "predicate"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_entities.id", ondelete="CASCADE", name="fk_rel_subject"),
        nullable=False,
    )
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_entities.id", ondelete="CASCADE", name="fk_rel_object"),
        nullable=False,
    )
    evidence_observation_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memory_observations.id", ondelete="SET NULL", name="fk_rel_evidence"),
        nullable=True,
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.9")
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    superseded_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memory_relations.id", name="fk_rel_superseded_by"),
        nullable=True,
    )


class MemoryRelationRejection(Base):
    """Dev-only log of relation upserts rejected for unknown predicates.

    Surfaced by a debug endpoint so the predicate vocabulary can be tuned.
    Never queried in the hot path.
    """

    __tablename__ = "memory_relation_rejections"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    subject_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicate: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rejected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
