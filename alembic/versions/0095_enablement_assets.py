"""enablement_assets table for Kai (Chiron) enablement knowledge store.

Revision ID: 0095
Revises: 0094
Create Date: 2026-06-16

Adds the ``enablement_assets`` table.  See ``artemis/enablement/models.py``
for the full ORM definition.  Key design choices:

- Primary unique key is ``drive_file_id`` (TEXT) — idempotent upsert anchor.
  Rows with no sheet-provided drive_file_id receive a synthesised slug key
  (prefix ``slug:``) at sync time.
- ``tags`` is a Postgres text ARRAY for multi-value tag filtering.
- ``embedding`` is a 384-dim pgvector VECTOR column (same model /  dims as
  the memory keystone: all-MiniLM-L6-v2).  NULL until the sync computes it.
- ``source_scope`` defaults to 'enablement'; set to 'shared' for cross-team
  assets other agents may surface.

Do NOT apply this migration until Lead merges the worker/kai-foundation branch
and runs ``uv run alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

try:
    from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
    _VECTOR_TYPE: object = Vector(384)
except ImportError:
    # Fallback for environments where pgvector isn't installed at migration time.
    # Alembic renders the column as TEXT; pgvector extension handles casting.
    _VECTOR_TYPE = sa.Text()

revision: str = "0095"
down_revision: str | None = "0094"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enablement_assets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # --- identity ---
        sa.Column("drive_file_id", sa.Text(), nullable=False),
        sa.Column("asset_name", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("drive_link", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tags", ARRAY(sa.Text()), nullable=True),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("transcript_link", sa.Text(), nullable=True),
        sa.Column("transcript_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("confidence_label", sa.Text(), nullable=True),
        sa.Column(
            "source_scope",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'enablement'"),
        ),
        # --- embedding (384-dim, all-MiniLM-L6-v2) ---
        sa.Column("embedding", _VECTOR_TYPE, nullable=True),
        # --- extra JSONB for any additional sheet columns ---
        sa.Column("extra", JSONB(), nullable=True),
        # --- timestamps ---
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drive_file_id", name="uq_enablement_assets_drive_file_id"),
    )
    op.create_index(
        "idx_enablement_assets_type",
        "enablement_assets",
        ["type"],
        unique=False,
    )
    op.create_index(
        "idx_enablement_assets_status",
        "enablement_assets",
        ["status"],
        unique=False,
    )
    op.create_index(
        "idx_enablement_assets_source_scope",
        "enablement_assets",
        ["source_scope"],
        unique=False,
    )
    op.create_index(
        "idx_enablement_assets_updated_at",
        "enablement_assets",
        ["updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_enablement_assets_updated_at", table_name="enablement_assets")
    op.drop_index("idx_enablement_assets_source_scope", table_name="enablement_assets")
    op.drop_index("idx_enablement_assets_status", table_name="enablement_assets")
    op.drop_index("idx_enablement_assets_type", table_name="enablement_assets")
    op.drop_table("enablement_assets")
