"""Crisis-content approval pipeline: link a thread-attached image (CCA10).

See ``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca10-slack-image-to-doc.md``.

Extends ``crisis_content_thread_notes`` (CCA9) with the two columns the
image-link flow needs, and adds one new additive ledger table.

``crisis_content_thread_notes.channel_id`` -- the Slack channel the reply
    landed in. Was already a parameter ``handle_thread_reply`` received
    (needed to post the nudge) but never persisted. CCA10 needs it back to
    call ``chat.getPermalink(channel, message_ts)`` for a note fetched later
    by ``thread_note_id`` alone -- see ``artemis.crisis_content.image_link``.
    Nullable because rows written before this migration have none (there is
    nothing to backfill: the channel isn't recoverable after the fact).

``crisis_content_thread_notes.file_count`` -- how many files this reply's
    Slack ``files[]`` array carried, computed by the events route the moment
    the event arrives (before ``files:read`` would ever matter -- this is
    array length, not file content). Needed for the "one reply, three files
    -> one line, wording says '3 images'" rule (see the brief's "Multiple
    images" section). ``has_attachment`` (CCA9) stays a plain boolean;
    ``file_count`` is the new, more specific signal. Defaults to 0 so
    pre-CCA10 rows read as "no known count" rather than NULL.

``crisis_content_image_link_deliveries`` -- the idempotency ledger for
    CCA10's single side-effecting action (the doc line + confirmation
    reply): one row per ``thread_note_id`` actually delivered. Mirrors
    ``crisis_content_writeback_deliveries`` (CCA7) exactly, minus the
    ``action`` column -- there is only one action here, so the unique
    constraint is on ``thread_note_id`` alone. See
    ``artemis.crisis_content.image_link`` for the model class and the
    check-before-writing / write-only-after-success discipline this table
    exists to support.

Revision ID: 0110
Revises: 0109
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0110"
down_revision: str | None = "0109"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMAGE_LINK_CONSTRAINT = "uq_crisis_content_image_link_deliveries_thread_note"


def upgrade() -> None:
    # ── Extend crisis_content_thread_notes (CCA9) ─────────────────────────
    op.add_column(
        "crisis_content_thread_notes", sa.Column("channel_id", sa.Text(), nullable=True)
    )
    op.add_column(
        "crisis_content_thread_notes",
        sa.Column(
            "file_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )

    # ── New table: crisis_content_image_link_deliveries ───────────────────
    op.create_table(
        "crisis_content_image_link_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_note_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "delivered_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_note_id"],
            ["crisis_content_thread_notes.id"],
            name="fk_crisis_content_image_link_thread_note",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_note_id", name=_IMAGE_LINK_CONSTRAINT),
    )


def downgrade() -> None:
    op.drop_table("crisis_content_image_link_deliveries")

    op.drop_column("crisis_content_thread_notes", "file_count")
    op.drop_column("crisis_content_thread_notes", "channel_id")
