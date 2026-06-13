"""Add proposed_actions table for the propose→confirm agency-writes gate.

Every action Artemis wants to take on Jon's behalf flows through this table.
A proposal is inserted in 'proposed' state; Jon's yes/no reply transitions it
to 'approved'/'rejected'; only 'approved' proposals can reach 'executed';
'failed' is the terminal error state; 'expired' fires when expires_at passes
without a reply.

Rows are never deleted (lossless / audit invariant).

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
        "proposed_actions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # Which integration will execute this action.
        sa.Column("action_type", sa.Text(), nullable=False),
        # Fully-drafted action payload — the EXACT thing that will be executed
        # on approval, so the thing approved is always the thing executed.
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        # Human-readable preview shown to Jon in the DM.
        sa.Column("preview", sa.Text(), nullable=False),
        # Lifecycle state.
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="proposed",
        ),
        # Slack user-id of the agent that proposed the action (for audit).
        sa.Column("requested_by", sa.Text(), nullable=False),
        # Slack user-id who will receive the approval DM (Jon).
        sa.Column("target_user_id", sa.Text(), nullable=False),
        # Result written back after execution (event link, issue key, etc.).
        sa.Column("executed_result", sa.dialects.postgresql.JSONB(), nullable=True),
        # Append-only audit log: list of {actor, action, at, note} dicts.
        sa.Column(
            "audit",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Proposal expires if no reply by this time (default 24 h from creation).
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
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
        sa.CheckConstraint(
            "action_type IN ("
            "'calendar.create',"
            "'calendar.update',"
            "'calendar.respond',"
            "'slack.send',"
            "'jira.create',"
            "'gmail.send'"
            ")",
            name="ck_proposed_actions_action_type",
        ),
        sa.CheckConstraint(
            "status IN ('proposed','approved','rejected','executed','failed','expired')",
            name="ck_proposed_actions_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_proposed_actions_target_status",
        "proposed_actions",
        ["target_user_id", "status"],
    )
    op.create_index(
        "idx_proposed_actions_expires",
        "proposed_actions",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_proposed_actions_expires", table_name="proposed_actions")
    op.drop_index("idx_proposed_actions_target_status", table_name="proposed_actions")
    op.drop_table("proposed_actions")
