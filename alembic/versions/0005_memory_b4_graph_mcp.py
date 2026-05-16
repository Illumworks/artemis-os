"""memory keystone B4 — graph layer (entities, relations, mentions) + extraction tracking

Additive to 0004. Adds:
  - graph_status, graph_attempt_count, graph_last_attempt_at on memory_observations
  - memory_entities table
  - memory_entity_aliases table
  - memory_entity_mentions table
  - memory_relations table
  - memory_relation_rejections table (dev-only predicate rejection log)

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Additive columns on memory_observations for graph extraction tracking
    op.execute("""
        ALTER TABLE memory_observations
            ADD COLUMN IF NOT EXISTS graph_status TEXT,
            ADD COLUMN IF NOT EXISTS graph_attempt_count INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS graph_last_attempt_at TIMESTAMPTZ
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_observations_graph_status
            ON memory_observations (graph_status)
            WHERE superseded_by IS NULL
    """)

    # Entities
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_entities (
            id              BIGSERIAL PRIMARY KEY,
            entity_kind     TEXT NOT NULL
                                CHECK (entity_kind IN
                                    ('person','project','brand','campaign','post','channel','other')),
            canonical_name  TEXT NOT NULL,
            name_slug       TEXT NOT NULL,
            scope_kind      TEXT NOT NULL,
            scope_id        TEXT NOT NULL,
            attributes      JSONB,
            first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            mention_count   INTEGER NOT NULL DEFAULT 1,
            confidence      REAL NOT NULL DEFAULT 0.9,
            superseded_by   BIGINT REFERENCES memory_entities(id),
            CONSTRAINT uq_entities_scope_kind_slug
                UNIQUE (scope_kind, scope_id, entity_kind, name_slug)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entities_scope
            ON memory_entities (scope_kind, scope_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_entities_kind_slug
            ON memory_entities (entity_kind, name_slug)
    """)

    # Entity aliases (surface forms)
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_entity_aliases (
            id          BIGSERIAL PRIMARY KEY,
            entity_id   BIGINT NOT NULL
                            REFERENCES memory_entities(id) ON DELETE CASCADE,
            alias       TEXT NOT NULL,
            alias_slug  TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_aliases_entity_slug UNIQUE (entity_id, alias_slug)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_aliases_slug
            ON memory_entity_aliases (alias_slug)
    """)

    # Entity mentions (entity ↔ observation or drawer)
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_entity_mentions (
            id              BIGSERIAL PRIMARY KEY,
            entity_id       BIGINT NOT NULL
                                REFERENCES memory_entities(id) ON DELETE CASCADE,
            source_kind     TEXT NOT NULL,
            source_id       BIGINT NOT NULL,
            mention_quote   TEXT,
            weight          REAL NOT NULL DEFAULT 1.0,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_mentions_entity_source
                UNIQUE (entity_id, source_kind, source_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_mentions_source
            ON memory_entity_mentions (source_kind, source_id)
    """)

    # Relations between entities
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_relations (
            id                      BIGSERIAL PRIMARY KEY,
            subject_id              BIGINT NOT NULL
                                        REFERENCES memory_entities(id) ON DELETE CASCADE,
            predicate               TEXT NOT NULL,
            object_id               BIGINT NOT NULL
                                        REFERENCES memory_entities(id) ON DELETE CASCADE,
            evidence_observation_id BIGINT
                                        REFERENCES memory_observations(id) ON DELETE SET NULL,
            weight                  REAL NOT NULL DEFAULT 1.0,
            confidence              REAL NOT NULL DEFAULT 0.9,
            first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            superseded_by           BIGINT REFERENCES memory_relations(id),
            CONSTRAINT uq_relations_triple UNIQUE (subject_id, predicate, object_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rel_subject
            ON memory_relations (subject_id, predicate)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rel_object
            ON memory_relations (object_id, predicate)
    """)

    # Predicate rejection log (dev-only debug surface)
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_relation_rejections (
            id          BIGSERIAL PRIMARY KEY,
            subject_id  BIGINT,
            predicate   TEXT NOT NULL,
            object_id   BIGINT,
            rejected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_relation_rejections")
    op.execute("DROP TABLE IF EXISTS memory_relations")
    op.execute("DROP TABLE IF EXISTS memory_entity_mentions")
    op.execute("DROP TABLE IF EXISTS memory_entity_aliases")
    op.execute("DROP TABLE IF EXISTS memory_entities")
    op.execute("DROP INDEX IF EXISTS idx_memory_observations_graph_status")
    op.execute("""
        ALTER TABLE memory_observations
            DROP COLUMN IF EXISTS graph_last_attempt_at,
            DROP COLUMN IF EXISTS graph_attempt_count,
            DROP COLUMN IF EXISTS graph_status
    """)
