"""Add lossless draft comments table.

Revision ID: 0075
Revises: 0074
Create Date: 2026-06-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "comments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("author_user_id", sa.BigInteger(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("anchor_start", sa.Integer(), nullable=True),
        sa.Column("anchor_end", sa.Integer(), nullable=True),
        sa.Column("anchored_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column(
            "mentions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_comments_status"),
        sa.ForeignKeyConstraint(
            ["author_user_id"],
            ["users.id"],
            name="fk_comments_author_user",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["campaign_deliverables.id"],
            name="fk_comments_draft",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["comments.id"],
            name="fk_comments_parent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_user_id"],
            ["users.id"],
            name="fk_comments_resolved_by_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_comments_draft_status", "comments", ["draft_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_comments_draft_status", table_name="comments")
    op.drop_table("comments")
