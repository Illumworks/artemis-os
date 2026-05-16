"""memory keystone tables — scopes, drawers, observations, evidence

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_scopes (
            scope_kind TEXT NOT NULL,
            scope_id   TEXT NOT NULL,
            display_name       TEXT,
            parent_scope_kind  TEXT,
            parent_scope_id    TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (scope_kind, scope_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_scopes_parent
            ON memory_scopes(parent_scope_kind, parent_scope_id)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_drawers (
            id           BIGSERIAL PRIMARY KEY,
            scope_kind   TEXT      NOT NULL,
            scope_id     TEXT      NOT NULL,
            corpus_kind  TEXT,
            content      TEXT      NOT NULL,
            content_hash TEXT      NOT NULL,
            source_kind  TEXT      NOT NULL,
            source_id    TEXT,
            source_extra JSONB,
            owner_user_id BIGINT,
            captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_drawers_scope_hash
                UNIQUE (scope_kind, scope_id, content_hash)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_drawers_scope
            ON memory_drawers(scope_kind, scope_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_drawers_source
            ON memory_drawers(source_kind, source_id)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_observations (
            id             BIGSERIAL PRIMARY KEY,
            scope_kind     TEXT  NOT NULL,
            scope_id       TEXT  NOT NULL,
            category       TEXT  NOT NULL DEFAULT 'discovery',
            content        TEXT  NOT NULL,
            content_hash   TEXT  NOT NULL,
            score          REAL  NOT NULL DEFAULT 1.0,
            hit_count      INT   NOT NULL DEFAULT 0,
            source_quality REAL  NOT NULL DEFAULT 0.5,
            user_confirmed BOOL  NOT NULL DEFAULT FALSE,
            valid_from     TIMESTAMPTZ,
            valid_until    TIMESTAMPTZ,
            superseded_by  BIGINT REFERENCES memory_observations(id),
            owner_user_id  BIGINT,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            accessed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_obs_scope_hash
                UNIQUE (scope_kind, scope_id, content_hash)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_observations_scope
            ON memory_observations(scope_kind, scope_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_observations_active
            ON memory_observations(scope_kind, scope_id)
            WHERE superseded_by IS NULL
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_observations_category
            ON memory_observations(category)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_observations_score
            ON memory_observations(score DESC)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_evidence (
            id             BIGSERIAL PRIMARY KEY,
            observation_id BIGINT NOT NULL
                REFERENCES memory_observations(id) ON DELETE CASCADE,
            source_kind    TEXT NOT NULL,
            source_id      BIGINT NOT NULL,
            source_quote   TEXT,
            weight         REAL NOT NULL DEFAULT 1.0,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_evidence_obs_source
                UNIQUE (observation_id, source_kind, source_id)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_evidence_observation
            ON memory_evidence(observation_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_evidence_source
            ON memory_evidence(source_kind, source_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_evidence")
    op.execute("DROP TABLE IF EXISTS memory_observations")
    op.execute("DROP TABLE IF EXISTS memory_drawers")
    op.execute("DROP TABLE IF EXISTS memory_scopes")
