"""Forge durable build runs + append-only event log.

Two tables:

  forge_runs     -- one row per tracked build job; run_id (TEXT UNIQUE) is used
                    as the WS room key and FK target so clients can replay logs
                    without knowing the numeric PK.
  forge_run_log  -- append-only event stream for a run; seq is monotonic per run.

Ares Brief 2 / Forge Phase 1 chunk 1.1.

Revision ID: 0103
Revises: 0102
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0103"
down_revision: str | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forge_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("dev_session_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["dev_session_id"],
            ["dev_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["dev_projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_forge_runs_run_id"),
    )
    op.create_index("ix_forge_runs_run_id", "forge_runs", ["run_id"], unique=False)
    op.create_index(
        "ix_forge_runs_dev_session_id", "forge_runs", ["dev_session_id"], unique=False
    )

    op.create_table(
        "forge_run_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["forge_runs.run_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_forge_run_log_run_seq", "forge_run_log", ["run_id", "seq"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_forge_run_log_run_seq", table_name="forge_run_log")
    op.drop_table("forge_run_log")
    op.drop_index("ix_forge_runs_dev_session_id", table_name="forge_runs")
    op.drop_index("ix_forge_runs_run_id", table_name="forge_runs")
    op.drop_table("forge_runs")
