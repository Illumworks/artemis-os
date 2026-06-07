"""Writing Studio tag registry tables + locked vocabulary seed.

Revision ID: 0070
Revises: 0069
Create Date: 2026-06-07
"""

from collections.abc import Sequence

from alembic import op
from artemis.writing_rules.tag_registry_seed import seed_tag_registry_sync

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_dimensions (
            id          BIGSERIAL PRIMARY KEY,
            key         TEXT NOT NULL,
            label       TEXT NOT NULL,
            active      BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT uq_tag_dimensions_key UNIQUE (key)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tag_dimensions_active_sort
            ON tag_dimensions(active, sort_order, id)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS tag_values (
            id            BIGSERIAL PRIMARY KEY,
            dimension_key TEXT NOT NULL REFERENCES tag_dimensions(key) ON DELETE RESTRICT,
            value         TEXT NOT NULL,
            label         TEXT NOT NULL,
            parent_value  TEXT,
            active        BOOLEAN NOT NULL DEFAULT TRUE,
            sort_order    INTEGER NOT NULL DEFAULT 0,
            metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_tag_values_dimension_value_parent
            ON tag_values(dimension_key, value, COALESCE(parent_value, ''))
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tag_values_dimension_active_sort
            ON tag_values(dimension_key, active, sort_order, id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_tag_values_parent_lookup
            ON tag_values(dimension_key, parent_value, sort_order, id)
    """)

    seed_tag_registry_sync(op.get_bind())


def downgrade() -> None:
    op.drop_index("idx_tag_values_parent_lookup", table_name="tag_values")
    op.drop_index("idx_tag_values_dimension_active_sort", table_name="tag_values")
    op.drop_index("uq_tag_values_dimension_value_parent", table_name="tag_values")
    op.drop_table("tag_values")

    op.drop_index("idx_tag_dimensions_active_sort", table_name="tag_dimensions")
    op.drop_table("tag_dimensions")
