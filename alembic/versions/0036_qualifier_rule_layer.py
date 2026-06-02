"""qualifier_rule_layer — audit table + skipped_signals visibility log.

Revision ID: 0036
Revises: 0035
Create Date: 2026-05-20

Creates:
  qualifier_rule_applications  — per-rule audit row (skip/suppress/boost)
  skipped_signals              — hard-skip visibility log for ops review
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision: str = "0036"
down_revision: str = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qualifier_rule_applications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column(
            "applied_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("from_priority", sa.Text(), nullable=True),
        sa.Column("to_priority", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_queue.id"],
            name="fk_qra_signal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_qra_signal_applied",
        "qualifier_rule_applications",
        ["signal_id", "applied_at"],
    )

    op.create_table(
        "skipped_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("district_id", sa.Text(), nullable=True),
        sa.Column("rule_id", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_queue.id"],
            name="fk_skipped_signal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_skipped_signals_district_created",
        "skipped_signals",
        ["district_id", "created_at"],
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_skipped_signals_district_created")
    op.execute("DROP TABLE IF EXISTS skipped_signals")
    op.execute("DROP INDEX IF EXISTS idx_qra_signal_applied")
    op.execute("DROP TABLE IF EXISTS qualifier_rule_applications")
