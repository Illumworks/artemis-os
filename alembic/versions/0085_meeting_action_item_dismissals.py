"""Add meeting_action_item_dismissals table — lossless dismiss record.

Keyed by (meeting_summary_id, action_item_key) where action_item_key is a
content hash of the normalized action-item text.  Rows are never deleted
(lossless invariant); a present row means "Jon said drop it, skip forever".

Also adds a 'dismissed' terminal state to the commitments CHECK constraint,
distinct from 'done' (no further follow-up nag, never reactivates).

Revision ID: 0085
Revises: 0084
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Widen the commitments status CHECK to include 'dismissed'.
    op.drop_constraint("ck_commitments_status", "commitments", type_="check")
    op.create_check_constraint(
        "ck_commitments_status",
        "commitments",
        "status IN ('active', 'snoozed', 'done', 'dismissed')",
    )

    # 2. Create the dismissals table.
    op.create_table(
        "meeting_action_item_dismissals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("meeting_summary_id", sa.BigInteger(), nullable=False),
        # Stable content hash (SHA-256 hex) of the normalised action-item text.
        sa.Column("action_item_key", sa.Text(), nullable=False),
        # Granola meeting ID — denormalised for fast lookup by ingest path
        # (avoids a JOIN when checking dismissal before inserting commitments).
        sa.Column("granola_id", sa.Text(), nullable=False),
        # The normalised text kept for human readability / audit.
        sa.Column("action_item_text", sa.Text(), nullable=False),
        sa.Column(
            "dismissed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["meeting_summary_id"],
            ["meeting_summaries.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "meeting_summary_id",
            "action_item_key",
            name="uq_meeting_action_item_dismissals",
        ),
    )
    op.create_index(
        "idx_meeting_action_item_dismissals_granola_id",
        "meeting_action_item_dismissals",
        ["granola_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_meeting_action_item_dismissals_granola_id",
        table_name="meeting_action_item_dismissals",
    )
    op.drop_table("meeting_action_item_dismissals")

    op.drop_constraint("ck_commitments_status", "commitments", type_="check")
    op.create_check_constraint(
        "ck_commitments_status",
        "commitments",
        "status IN ('active', 'snoozed', 'done')",
    )
