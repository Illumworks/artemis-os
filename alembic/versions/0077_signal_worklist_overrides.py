"""Add signal_worklist_overrides for Signals worklist edits.

Revision ID: 0077_signal_worklist_overrides
Revises: 0076
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0077_signal_worklist_overrides"
down_revision = "0076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_worklist_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("worklist_cluster_key", sa.Text(), nullable=True),
        sa.Column("hidden_from_worklist", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("updated_by", sa.Text(), nullable=True),
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
            ["signal_id"],
            ["signal_queue.id"],
            name="fk_signal_worklist_overrides_signal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_signal_worklist_overrides"),
        sa.UniqueConstraint("signal_id", name="uq_signal_worklist_overrides_signal"),
    )
    op.create_index(
        "idx_signal_worklist_overrides_hidden",
        "signal_worklist_overrides",
        ["hidden_from_worklist"],
    )
    op.create_index(
        "idx_signal_worklist_overrides_cluster_key",
        "signal_worklist_overrides",
        ["worklist_cluster_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_signal_worklist_overrides_cluster_key",
        table_name="signal_worklist_overrides",
    )
    op.drop_index(
        "idx_signal_worklist_overrides_hidden",
        table_name="signal_worklist_overrides",
    )
    op.drop_table("signal_worklist_overrides")
