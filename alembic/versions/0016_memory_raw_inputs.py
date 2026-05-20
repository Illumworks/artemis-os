"""raw_inputs — append-only verbatim capture with SHA-256 hash chain.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-18

Phase M1: Lossless memory foundation.
  - raw_inputs: append-only, hash-chained source record for every memory write.
    payload is nullable so archiving can NULL it while keeping the row as a
    hash-chain placeholder. payload_hash is preserved after archiving.
  - memory_observations.raw_input_id: nullable FK to raw_inputs (backward compat;
    all new observations get one). ON DELETE SET NULL so truncating raw_inputs
    in tests doesn't cascade-delete observations.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "raw_inputs",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("source_kind", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=True),
        sa.Column("actor", sa.Text, nullable=True),
        sa.Column("scope_kind", sa.Text, nullable=False),
        sa.Column("scope_id", sa.Text, nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("payload_hash", sa.Text, nullable=False),
        sa.Column("prev_hash", sa.Text, nullable=True),
        sa.Column("this_hash", sa.Text, nullable=False),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_raw_inputs_scope",
        "raw_inputs",
        ["scope_kind", "scope_id", "created_at"],
    )
    op.create_index(
        "ix_raw_inputs_source",
        "raw_inputs",
        ["source_kind", "source_id"],
    )

    # Add raw_input_id to memory_observations (nullable; every new write gets one).
    op.add_column(
        "memory_observations",
        sa.Column("raw_input_id", sa.BigInteger, nullable=True),
    )
    op.create_foreign_key(
        "fk_obs_raw_input",
        "memory_observations",
        "raw_inputs",
        ["raw_input_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_obs_raw_input", "memory_observations", type_="foreignkey")
    op.drop_column("memory_observations", "raw_input_id")
    op.drop_index("ix_raw_inputs_source", table_name="raw_inputs")
    op.drop_index("ix_raw_inputs_scope", table_name="raw_inputs")
    op.drop_table("raw_inputs")
