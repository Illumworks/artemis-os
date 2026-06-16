"""Add agent_pending_asks table for hub escalation layer (Phase 1).

Records asks from named agents (Kai, Callie, …) that @-mention Jon or pose
questions to him.  The escalation sweep (hourly) fires after ~1 day and has
Artemis post a terminal connective comment + DM Jon.

Revision ID: 0097
Revises: 0096
Create Date: 2026-06-16

NOTE: down_revision is 0096 (trace-capture migration, built in parallel).
      Do NOT run `alembic upgrade` in this worktree — Lead sequences migrations
      on merge after all parallel branches land.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_pending_asks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # The named agent that posted the ask.
        sa.Column("agent_id", sa.Text(), nullable=False),
        # Slack channel where the ask lives.
        sa.Column("channel_id", sa.Text(), nullable=False),
        # Slack message timestamp of the ask (the ``ts`` field).
        sa.Column("message_ts", sa.Text(), nullable=False),
        # Short human-readable summary surfaced in the escalation DM / brief.
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Set when Jon replies — NULL means unresolved.
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Set when Artemis has already escalated this ask — prevents double-fire.
        sa.Column("escalated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "channel_id",
            "message_ts",
            name="uq_agent_pending_asks_key",
        ),
    )
    op.create_index(
        "idx_agent_pending_asks_unresolved",
        "agent_pending_asks",
        ["resolved_at", "created_at"],
    )
    op.create_index(
        "idx_agent_pending_asks_channel",
        "agent_pending_asks",
        ["channel_id", "resolved_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_agent_pending_asks_channel", table_name="agent_pending_asks")
    op.drop_index("idx_agent_pending_asks_unresolved", table_name="agent_pending_asks")
    op.drop_table("agent_pending_asks")
