"""SQLAlchemy 2.x async ORM models for the memory keystone.

Core tables:
  memory_scopes                — scope catalog (UX helper, not query-critical)
  memory_drawers               — verbatim layer; immutable evidence floor
  memory_observations          — curated layer; what retrieval reads at prompt time
  memory_evidence              — many-to-many link: drawer/obs → observation
  memory_observation_scopes    — MW1: many-to-many join: observation ↔ scope

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
    (superseded_by IS NULL) keeps active-only queries fast.

    M2 additions:
      confidence     — belief in claim (0.0–1.0); CHECK enforced in DB.
      supersedes     — FK to the prior observation this one replaces (M2 supersession chain).
      evidence_count — corroborating raw_inputs count; incremented on corroboration.
    """

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
    # M1: FK to raw_inputs — nullable for backward compat; every new observation gets one.
    raw_input_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("raw_inputs.id", name="fk_obs_raw_input", ondelete="SET NULL"),
        nullable=True,
    )

    # M2: validity + confidence + supersession chain
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    supersedes: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memory_observations.id", name="fk_obs_supersedes", ondelete="SET NULL"),
        nullable=True,
    )
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    # Graph extraction tracking (B4 additive columns)
    graph_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    graph_last_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # MW1: multi-scope metadata
    wing: Mapped[str] = mapped_column(Text, nullable=False, server_default="durable")
    confidence_origin: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    # CC28: widened from BigInteger to Text — supports slugs, UUIDs, numeric strings.
    # Existing rows had BigInt values; migration 0049 stringified them (e.g. 182 → "182").
    # Rows written before CC28 with SHA-256 hashes (MC3/MC4/MC5 smokes: obs ids 29–31)
    # retain their hash strings verbatim — lossless invariant, do not modify.
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
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


class MemoryObservationScope(Base):
    """MW1: many-to-many join between memory_observations and memory_scopes.

    One row per (observation_id, scope_kind, scope_id). is_primary=True marks
    the primary scope — the one that also lives in memory_observations.scope_kind
    / scope_id for backward-compat reads (M6 endpoints keep working via those
    legacy columns). Secondary scopes only exist in this table.

    LOSSLESS: rows are never deleted directly; CASCADE from observation delete
    (which is itself never called) is the only removal path.
    """

    __tablename__ = "memory_observation_scopes"
    __table_args__ = (
        Index("idx_memory_observation_scopes_obs", "observation_id"),
        Index("idx_memory_observation_scopes_scope", "scope_kind", "scope_id"),
    )

    observation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "memory_observations.id",
            name="fk_obs_scopes_observation",
            ondelete="CASCADE",
        ),
        primary_key=True,
    )
    scope_kind: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        nullable=False,
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

    # M2 additive columns
    valid_from: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    entity_evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    entity_supersedes: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("memory_entities.id", name="fk_entity_supersedes", ondelete="SET NULL"),
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


# ── Memory M2 — Conflict detection ───────────────────────────────────────────


class MemoryConflict(Base):
    """Records when two observations make contradictory claims.

    The pair (observation_a_id, observation_b_id) is stored sorted (LEAST/GREATEST)
    at the DB level via a functional unique index — the ORM normalises the pair
    before insert. resolution=NULL means unresolved; auto-resolution sets 'auto'.
    LOSSLESS: resolving a conflict does NOT delete observations; it sets valid_until
    on the losing observation and updates supersedes on the winner.
    """

    __tablename__ = "memory_conflicts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    scope_id: Mapped[str] = mapped_column(Text, nullable=False)
    observation_a_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_observations.id", name="fk_conflict_obs_a", ondelete="CASCADE"),
        nullable=False,
    )
    observation_b_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("memory_observations.id", name="fk_conflict_obs_b", ondelete="CASCADE"),
        nullable=False,
    )
    conflict_type: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
