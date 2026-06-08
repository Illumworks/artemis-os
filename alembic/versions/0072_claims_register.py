"""Add structured claims register table.

Revision ID: 0072
Revises: 0071
Create Date: 2026-06-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0072"
down_revision: str | None = "0071"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=False),
        sa.Column("claim_code", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.Column("approved_phrasing", sa.Text(), nullable=False),
        sa.Column("packaging", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="approved", nullable=False),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("tier IS NULL OR tier BETWEEN 1 AND 4", name="ck_claims_tier_range"),
        sa.CheckConstraint(
            "status IN ('proposed', 'approved', 'retired')",
            name="ck_claims_status",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["writing_profiles.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["claims.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "claim_code", name="uq_claims_profile_code"),
    )
    op.create_index("idx_claims_profile_status", "claims", ["profile_id", "status"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_claims_profile_status", table_name="claims")
    op.drop_table("claims")
