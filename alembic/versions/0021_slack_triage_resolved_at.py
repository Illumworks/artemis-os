"""slack_triage_resolved_at — add resolved_at column to slack_inbound_messages.

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-19

J9: Slack triage workflow. resolved_at persists resolution state so resolved
items disappear from missedMentions counts and the triage queue without
deleting rows (lossless memory rule).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "slack_inbound_messages",
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_slack_inbound_resolved_at",
        "slack_inbound_messages",
        ["resolved_at"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("ix_slack_inbound_resolved_at", table_name="slack_inbound_messages")
    op.drop_column("slack_inbound_messages", "resolved_at")
