"""Add directory_people table for name→email resolution.

A cache of company roster entries (synced from Slack) so agents and the
post-meeting scheduler can map a person's NAME to their EMAIL. Keyed on a
lowercased email (unique).

Revision ID: 0100
Revises: 0099
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0100"
down_revision: str | None = "0099"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "directory_people",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("slack_user_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="slack"),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_directory_people_email",
        "directory_people",
        ["email"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_directory_people_email", table_name="directory_people")
    op.drop_table("directory_people")
