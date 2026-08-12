"""Callie's guarded DM send: audit trail (CALLIE-1).

Revision ID: 0115
Revises: 0114

**Numbering note.** Main is at 0113 as of this branch's creation. This
revision is deliberately numbered 0115 with ``down_revision="0114"`` per
explicit instruction, even though no 0114 migration exists in this worktree
-- it is claimed by a concurrently-running slice on another branch. Running
``alembic upgrade head`` here will fail with "Can't locate revision(s)
'0114'" until that slice merges to main; this is expected given the
instruction, not a defect in this migration. See the CALLIE-1 report for the
verbatim failure. Do NOT renumber this to 0114 (or anything else) to make it
pass locally in isolation -- that is exactly the collision 0112/CCA14/CCA15
already had once (see 0113's docstring for that history), and the fix each
time was NOT to silently pick a number that merely completes on the current
branch.

One new table, purely additive:

``callie_dm_send_attempts`` -- append-only audit of every send_guarded_dm
    call (artemis.floating_artemis.tools.callie_dm), outcome sent / refused /
    error, per the brief's hard requirement 4: "Audit every attempt...
    Both sends and refusals; a refusal nobody can see is how you find out
    too late." No UPDATE/DELETE path exists anywhere in this codebase for
    this table (CLAUDE.md rule 3 -- lossless memory / evidence).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0115"
down_revision: str | None = "0114"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "callie_dm_send_attempts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("requester_slack_user_id", sa.Text(), nullable=True),
        sa.Column("requester_email", sa.Text(), nullable=True),
        sa.Column("recipient_input", sa.Text(), nullable=False),
        sa.Column("recipient_email", sa.Text(), nullable=True),
        sa.Column("recipient_slack_user_id", sa.Text(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("sent_text", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("refusal_reason", sa.Text(), nullable=True),
        sa.Column("slack_ts", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('sent', 'refused', 'error')",
            name="ck_callie_dm_send_attempts_outcome",
        ),
    )
    op.create_index(
        "ix_callie_dm_send_attempts_created_at",
        "callie_dm_send_attempts",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_callie_dm_send_attempts_created_at",
        table_name="callie_dm_send_attempts",
    )
    op.drop_table("callie_dm_send_attempts")
