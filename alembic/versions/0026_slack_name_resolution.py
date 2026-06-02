"""slack_name_resolution — cache tables for user/channel names + mention_type filter.

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-19

J9b: Slack triage polish.
  - slack_users: cache Slack user info (id → name/real_name)
  - slack_channels: cache Slack channel info (id → name, is_im)
  - mention_type column on slack_inbound_messages: 'direct'|'channel'|'group'|'keyword'
    so the triage queue can filter to true @-you direct mentions only.
  - Backfill: existing rows default to 'direct' (conservative — they were stored
    because they matched app_mention / DM patterns; real backfill requires the
    message text which is already stored so a follow-up script can refine).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026"
down_revision: str = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── slack_users ────────────────────────────────────────────────────────────
    op.create_table(
        "slack_users",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("real_name", sa.Text, nullable=True),
        sa.Column("is_bot", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── slack_channels ─────────────────────────────────────────────────────────
    op.create_table(
        "slack_channels",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("is_im", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("is_private", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── mention_type on slack_inbound_messages ────────────────────────────────
    op.add_column(
        "slack_inbound_messages",
        sa.Column("mention_type", sa.Text, nullable=True),
    )
    # Backfill: rows that pre-date this column were stored as app_mention or DM
    # events; mark them 'direct' so they remain visible in the triage queue.
    op.execute(
        "UPDATE slack_inbound_messages SET mention_type = 'direct' WHERE mention_type IS NULL"
    )
    # Add index for the filter we'll use most: WHERE mention_type = 'direct'
    op.create_index(
        "ix_slack_inbound_mention_type",
        "slack_inbound_messages",
        ["mention_type"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("ix_slack_inbound_mention_type", table_name="slack_inbound_messages")
    op.drop_column("slack_inbound_messages", "mention_type")
    op.drop_table("slack_channels")
    op.drop_table("slack_users")
