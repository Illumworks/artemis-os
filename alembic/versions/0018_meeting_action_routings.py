"""meeting_action_routings + personal_todos tables.

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-18

J6c post-meeting workflow:
  - meeting_action_routings: idempotent record of every action taken on a
    meeting action item (to Jira / OKR / Slack / Todo). UNIQUE on
    (meeting_id, action_text, routed_to) prevents double-routing.
  - personal_todos: local-only todo items created from meeting action items
    (and potentially other surfaces). Separate table so it can grow
    independently of the notifications stub.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018"
down_revision: str = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meeting_action_routings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("meeting_id", sa.Text, nullable=False),
        sa.Column("action_text", sa.Text, nullable=False),
        sa.Column("routed_to", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=True),
        sa.Column("target_url", sa.Text, nullable=True),
        sa.Column(
            "routed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "meeting_id", "action_text", "routed_to", name="uq_mar_meeting_action_dest"
        ),
    )
    op.create_index(
        "ix_mar_meeting_id",
        "meeting_action_routings",
        ["meeting_id"],
    )

    op.create_table(
        "personal_todos",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column("done", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("done_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("personal_todos")
    op.drop_index("ix_mar_meeting_id", table_name="meeting_action_routings")
    op.drop_table("meeting_action_routings")
