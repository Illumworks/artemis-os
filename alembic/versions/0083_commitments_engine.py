"""Add commitments lifecycle table for proactive follow-ups.

Revision ID: 0083
Revises: 0082
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commitments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column("due", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "sensitivity",
            sa.Text(),
            nullable=False,
            server_default="personal_ops",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="active",
        ),
        sa.Column("snoozed_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('active', 'snoozed', 'done')",
            name="ck_commitments_status",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type",
            "source_id",
            "text",
            name="uq_commitments_source_text",
        ),
    )
    op.create_index(
        "idx_commitments_status_due",
        "commitments",
        ["status", "due"],
        unique=False,
    )
    op.create_index(
        "idx_commitments_owner",
        "commitments",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_commitments_owner", table_name="commitments")
    op.drop_index("idx_commitments_status_due", table_name="commitments")
    op.drop_table("commitments")
