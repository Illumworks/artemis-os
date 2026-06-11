"""Add morning brief delivery reservation table.

Revision ID: 0080
Revises: 0079
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0080"
down_revision: str | None = "0079"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "morning_brief_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("delivery_kind", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("recipient_id", sa.Text(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="reserved"),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "reserved_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("delivered_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'sent', 'failed')",
            name="ck_morning_brief_deliveries_status",
        ),
        sa.ForeignKeyConstraint(["snapshot_id"], ["brief_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "delivery_kind",
            "provider",
            "recipient_id",
            "delivery_date",
            name="uq_morning_brief_delivery_once_per_day",
        ),
    )
    op.create_index(
        "idx_morning_brief_deliveries_lookup",
        "morning_brief_deliveries",
        ["delivery_date", "provider", "recipient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_morning_brief_deliveries_lookup", table_name="morning_brief_deliveries")
    op.drop_table("morning_brief_deliveries")
