"""Add radar_surfaced_items table for awaiting-reply radar dedup + dismiss.

Each row tracks one item (Slack mention or Gmail thread) that Artemis has
surfaced to Jon.  Rows are never deleted (lossless invariant); a dismissed
row stops the item from re-nagging.

Revision ID: 0086
Revises: 0085
Create Date: 2026-06-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0086"
down_revision: str | None = "0085"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "radar_surfaced_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # "slack_mention" | "gmail_thread"
        sa.Column("item_type", sa.Text(), nullable=False),
        # Stable opaque key for dedup.
        # Slack: "<channel_id>:<thread_ts>"  Gmail: "<threadId>"
        sa.Column("item_key", sa.Text(), nullable=False),
        # Human-readable label stored for audit/display, never used for logic.
        # Slack: channel name + sender.  Gmail: subject + sender.  Kept short.
        sa.Column("label", sa.Text(), nullable=False),
        # Permalink (Slack) or web link (Gmail) — surfaced in the nudge.
        sa.Column("permalink", sa.Text(), nullable=True),
        # When this item was first surfaced to Jon.
        sa.Column(
            "first_surfaced_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Updated each time the item is re-nagged (if not dismissed).
        sa.Column(
            "last_surfaced_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Set when Jon dismisses — NULL means still active.
        sa.Column("dismissed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_type", "item_key", name="uq_radar_surfaced_item_type_key"),
    )
    op.create_index(
        "idx_radar_surfaced_items_active",
        "radar_surfaced_items",
        ["item_type", "dismissed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_radar_surfaced_items_active", table_name="radar_surfaced_items")
    op.drop_table("radar_surfaced_items")
