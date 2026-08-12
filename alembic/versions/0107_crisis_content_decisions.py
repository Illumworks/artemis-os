"""Crisis-content approval pipeline: decisions table (slice B2c, CCA5).

One additive table. See ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca5-approval-loop.md``.

``crisis_content_decisions`` -- append-only (``CLAUDE.md`` rule 3, lossless
    memory). No code path in this repo ever UPDATEs or DELETEs a row here: a
    changed mind (``changes_requested`` followed later by ``approved``) is a
    new row, not an edit. Deliberately NO unique constraint on
    ``(card_id, route)`` -- that would make the second, legitimate row
    impossible to insert. An index on ``(card_id, route)`` supports the
    "what's the latest decision for this route" read
    (``artemis.crisis_content.decisions.get_latest_decision``) without
    forcing uniqueness.

Revision ID: 0107
Revises: 0106
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0107"
down_revision: str | None = "0106"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CARD_ROUTE_INDEX = "ix_crisis_content_decisions_card_route"


def upgrade() -> None:
    op.create_table(
        "crisis_content_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("decided_by_slack_user_id", sa.Text(), nullable=False),
        sa.Column("decided_by_email", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("slack_message_ts", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["crisis_content_cards.id"],
            name="fk_crisis_content_decisions_card",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        _CARD_ROUTE_INDEX,
        "crisis_content_decisions",
        ["card_id", "route"],
    )


def downgrade() -> None:
    op.drop_index(_CARD_ROUTE_INDEX, table_name="crisis_content_decisions")
    op.drop_table("crisis_content_decisions")
