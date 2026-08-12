"""Crisis-content approval pipeline: write-back delivery ledger (CCA7).

One additive table. See ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca7-writeback-and-notify-jen.md``.

``crisis_content_writeback_deliveries`` -- the per-action idempotency ledger
    for CCA7's write-back: one row per ``(decision_id, action)`` actually
    delivered, where ``action`` is one of ``doc_line`` / ``comment`` /
    ``email``. A decision must produce exactly one doc line, one Drive
    comment, and one Gmail backup, even if the handler runs twice -- the
    three actions are checked and recorded INDEPENDENTLY (via the unique
    constraint below), so a failure delivering the email can never cause the
    doc line to be re-inserted on retry. See
    ``artemis.crisis_content.writeback`` for the model class and the
    check-before-each-of-three-actions logic that reads/writes this table.

    Written only after the corresponding side effect (Docs insertText,
    Drive comments.create, or Gmail messages.send) has actually succeeded --
    never before -- mirroring ``crisis_content_notifications``' own
    "mark only after a successful post" discipline
    (``artemis.crisis_content.transitions.mark_notified``).

Revision ID: 0108
Revises: 0107
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0108"
down_revision: str | None = "0107"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DECISION_ACTION_CONSTRAINT = "uq_crisis_content_writeback_decision_action"


def upgrade() -> None:
    op.create_table(
        "crisis_content_writeback_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("decision_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "delivered_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            ["crisis_content_decisions.id"],
            name="fk_crisis_content_writeback_decision",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_id", "action", name=_DECISION_ACTION_CONSTRAINT
        ),
    )
    op.create_index(
        "ix_crisis_content_writeback_deliveries_decision_id",
        "crisis_content_writeback_deliveries",
        ["decision_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crisis_content_writeback_deliveries_decision_id",
        table_name="crisis_content_writeback_deliveries",
    )
    op.drop_table("crisis_content_writeback_deliveries")
