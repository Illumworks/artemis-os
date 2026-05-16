"""memory keystone B2 — FTS columns + embeddings table

Additive to 0002. Adds:
  - content_fts TSVECTOR generated column on memory_drawers and memory_observations
  - GIN indexes on both content_fts columns
  - memory_embeddings table (target_table, target_id, model_version, embedding vector(384))
  - HNSW index on memory_embeddings.embedding with vector_cosine_ops

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # FTS on drawers
    op.execute("""
        ALTER TABLE memory_drawers
            ADD COLUMN IF NOT EXISTS content_fts TSVECTOR
            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_drawers_fts
            ON memory_drawers USING GIN (content_fts)
    """)

    # FTS on observations
    op.execute("""
        ALTER TABLE memory_observations
            ADD COLUMN IF NOT EXISTS content_fts TSVECTOR
            GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_observations_fts
            ON memory_observations USING GIN (content_fts)
    """)

    # Embeddings table
    op.execute("""
        CREATE TABLE IF NOT EXISTS memory_embeddings (
            id           BIGSERIAL PRIMARY KEY,
            target_table TEXT    NOT NULL CHECK (target_table IN ('drawer', 'observation')),
            target_id    BIGINT  NOT NULL,
            model_version TEXT   NOT NULL,
            embedding    vector(384),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_embeddings_target_model
                UNIQUE (target_table, target_id, model_version)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_embeddings_target
            ON memory_embeddings (target_table, target_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_embeddings_hnsw
            ON memory_embeddings USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_embeddings")
    op.execute("ALTER TABLE memory_observations DROP COLUMN IF EXISTS content_fts")
    op.execute("ALTER TABLE memory_drawers DROP COLUMN IF EXISTS content_fts")
