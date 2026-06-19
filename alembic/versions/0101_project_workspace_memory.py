"""Add project_workspace_memory table for Ares per-project one-brain drawer.

Stores plan, append-only decisions log, file map snapshot, progress text, and
open threads so a new coding session can inherit prior context with no
re-briefing. One row per project (UNIQUE on project_id, CASCADE on delete).

Revision ID: 0101
Revises: 0100
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0101"
down_revision: str | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "project_workspace_memory",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("plan", sa.Text(), nullable=True),
        sa.Column(
            "decisions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "file_map",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("progress", sa.Text(), nullable=True),
        sa.Column(
            "open_threads",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["dev_projects.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", name="uq_project_workspace_memory_project"),
    )
    op.create_index(
        "ix_project_workspace_memory_project",
        "project_workspace_memory",
        ["project_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_workspace_memory_project",
        table_name="project_workspace_memory",
    )
    op.drop_table("project_workspace_memory")
