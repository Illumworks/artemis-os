"""Crisis-content approval pipeline: card lifecycle (CCA9).

See ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca9-card-lifecycle.md``.

Extends ``crisis_content_notifications`` (posted-message location + the
copy_hash that fixes the re-approval hole) and adds one new append-only
table, ``crisis_content_thread_notes``.

**Why the re-approval hole exists (the bug this migration part-fixes):**
copy hits ``Ready`` -> card posts -> an approver requests changes -> Jen
rewrites the copy -> nothing happens ever again. Her chip still reads
``Ready``, so the existing ``(card_id, route, status_value)`` unique
constraint would dedupe any re-fire even if the transition-detection code
decided to re-emit. Extending the constraint to
``(card_id, route, status_value, copy_hash)`` is what makes a genuine
revision (new ``copy_hash``) distinguishable from the original notification
it are replacing at the ledger level; the transition-detection logic in
``artemis.crisis_content.transitions`` supplies the other half (deciding
WHEN to re-emit).

**Backfill before constraint, in that order.** The two live notification
rows predate ``copy_hash`` entirely. Postgres unique constraints treat NULL
as distinct from any other NULL, so a bare NULL would not itself violate
the new constraint -- the backfill is not about avoiding a migration
failure, it is about correctness afterward: ``has_notified`` compares
against the CURRENT card's ``copy_hash``, and a NULL ledger row would never
equal it, so the original ("Ready" the first time) notification would
permanently misread as "never notified" and be eligible to fire again on
the very next poll tick, even with no revision at all. The backfill reads
the CURRENT ``crisis_content_cards.copy_hash`` for each existing row's
``card_id`` -- correct here because, prior to this migration, nothing could
ever re-fire (that is the bug this slice fixes), so each existing
notification's card still carries the exact copy that was notified on.

``crisis_content_thread_notes`` -- append-only (``CLAUDE.md`` rule 3,
    lossless memory). No code path in this repo ever UPDATEs or DELETEs a
    row here. Unique on ``(card_id, message_ts)`` so a Slack retry of the
    same reply cannot duplicate a note -- see
    ``artemis.crisis_content.thread_notes``.

Revision ID: 0109
Revises: 0108
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0109"
down_revision: str | None = "0108"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOTIFICATIONS_CONSTRAINT = "uq_crisis_content_notifications_card_route_status"
_THREAD_NOTES_CONSTRAINT = "uq_crisis_content_thread_notes_card_message_ts"
_MESSAGE_TS_INDEX = "ix_crisis_content_notifications_message_ts"
_THREAD_NOTES_CARD_INDEX = "ix_crisis_content_thread_notes_card_id"


def upgrade() -> None:
    # ── Extend crisis_content_notifications ───────────────────────────────
    op.add_column(
        "crisis_content_notifications", sa.Column("channel_id", sa.Text(), nullable=True)
    )
    op.add_column(
        "crisis_content_notifications", sa.Column("message_ts", sa.Text(), nullable=True)
    )
    op.add_column(
        "crisis_content_notifications", sa.Column("copy_hash", sa.Text(), nullable=True)
    )

    # Backfill BEFORE widening the unique constraint -- see module docstring.
    op.execute(
        "UPDATE crisis_content_notifications AS n "
        "SET copy_hash = c.copy_hash "
        "FROM crisis_content_cards AS c "
        "WHERE c.id = n.card_id AND n.copy_hash IS NULL"
    )

    op.drop_constraint(_NOTIFICATIONS_CONSTRAINT, "crisis_content_notifications", type_="unique")
    op.create_unique_constraint(
        _NOTIFICATIONS_CONSTRAINT,
        "crisis_content_notifications",
        ["card_id", "route", "status_value", "copy_hash"],
    )
    # Thread-reply lookups (find_card_thread_target) hit this on every reply.
    op.create_index(
        _MESSAGE_TS_INDEX, "crisis_content_notifications", ["message_ts"]
    )

    # ── New table: crisis_content_thread_notes ────────────────────────────
    op.create_table(
        "crisis_content_thread_notes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("route", sa.Text(), nullable=True),
        sa.Column("slack_user_id", sa.Text(), nullable=False),
        sa.Column("author_email", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "has_attachment", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("message_ts", sa.Text(), nullable=False),
        # The parent thread this reply landed in. Needed so "nudge once" can be
        # scoped per THREAD rather than per card: one card can have two routes
        # posted to two different places (asset -> Jon's DM, copy -> the
        # channel), and a re-fired card starts a brand-new thread. Card-scoped
        # dedup would silently skip the nudge on a thread's genuinely first
        # reply, which is exactly the case where someone is most likely to
        # believe their reply counted as an approval.
        sa.Column("thread_ts", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["crisis_content_cards.id"],
            name="fk_crisis_content_thread_notes_card",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_id", "message_ts", name=_THREAD_NOTES_CONSTRAINT),
    )
    op.create_index(
        _THREAD_NOTES_CARD_INDEX, "crisis_content_thread_notes", ["card_id"]
    )


def downgrade() -> None:
    op.drop_index(_THREAD_NOTES_CARD_INDEX, table_name="crisis_content_thread_notes")
    op.drop_table("crisis_content_thread_notes")

    op.drop_index(_MESSAGE_TS_INDEX, table_name="crisis_content_notifications")
    op.drop_constraint(_NOTIFICATIONS_CONSTRAINT, "crisis_content_notifications", type_="unique")
    op.create_unique_constraint(
        _NOTIFICATIONS_CONSTRAINT,
        "crisis_content_notifications",
        ["card_id", "route", "status_value"],
    )
    op.drop_column("crisis_content_notifications", "copy_hash")
    op.drop_column("crisis_content_notifications", "message_ts")
    op.drop_column("crisis_content_notifications", "channel_id")
