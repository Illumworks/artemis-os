"""Crisis-content approval pipeline: transition detection persistence (slice B1).

Three additive tables backing ``artemis/crisis_content/transitions.py`` --
no changes to any existing table. See
``docs/crisis-content-approval-pipeline.md`` and
``briefs/cca2-transition-detection.md``.

``crisis_content_cards`` -- latest observed state, one row per card
    identity (``identity_header``, ``identity_platform``,
    ``identity_ordinal``). ``identity_platform`` is nullable -- Jen's
    platform chip can be unset.

    Nullable-uniqueness decision: Postgres treats NULLs as distinct values
    in a plain UNIQUE constraint/index, so a plain
    ``UNIQUE(identity_header, identity_platform, identity_ordinal)`` would
    let two platform-less rows sharing the same ``(header, ordinal)`` both
    insert -- the exact silent-duplicate bug this migration must not ship.
    We use a COALESCE-based expression unique index instead of normalizing
    unset platform to a sentinel string in the column. Reasoning: the
    column stays genuinely nullable, matching the schema this migration was
    briefed against and letting every reader (ORM, ad-hoc SQL, a future
    report) get a real ``None``/``NULL`` for "platform not set" instead of
    a magic string it would have to know to filter. The expression index
    treats ``NULL`` and ``NULL`` as equal for uniqueness purposes without
    ever writing that sentinel into the data itself. ``COALESCE(..., '')``
    is safe because the parser only ever produces ``None`` or a non-empty
    stripped string for platform (see
    ``artemis/crisis_content/parser.py::_parse_status_lines`` --
    ``platform_match.group(1).strip() or None``), so ``''`` can never
    collide with a legitimate value.

``crisis_content_copy_versions`` -- append-only (``CLAUDE.md`` rule 3,
    lossless memory). No code path in this repo ever UPDATEs or DELETEs a
    row here. Unique on ``(card_id, copy_hash)`` so re-observing an
    unchanged hash is a no-op rather than a duplicate row.

``crisis_content_notifications`` -- the dedup ledger for "have we already
    notified on this ``(card, route, status)``". Written by ``mark_notified``
    (called by slice B2 after a successful Slack post); slice B1 only reads
    it for suppression decisions.

Revision ID: 0106
Revises: 0105
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0106"
down_revision: str | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IDENTITY_INDEX = "uq_crisis_content_cards_identity"


def upgrade() -> None:
    op.create_table(
        "crisis_content_cards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("identity_header", sa.Text(), nullable=False),
        sa.Column("identity_platform", sa.Text(), nullable=True),
        sa.Column("identity_ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("asset_status", sa.Text(), nullable=True),
        sa.Column("copy_status", sa.Text(), nullable=True),
        sa.Column("asset_url", sa.Text(), nullable=True),
        sa.Column("copy_hash", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Expression unique index, not a plain UniqueConstraint -- see the
    # "Nullable-uniqueness decision" note in the module docstring above.
    op.execute(
        f"CREATE UNIQUE INDEX {_IDENTITY_INDEX} ON crisis_content_cards "
        "(identity_header, COALESCE(identity_platform, ''), identity_ordinal)"
    )

    op.create_table(
        "crisis_content_copy_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("copy_hash", sa.Text(), nullable=False),
        sa.Column("copy_body", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["crisis_content_cards.id"],
            name="fk_copy_versions_card",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "card_id", "copy_hash", name="uq_crisis_content_copy_versions_card_hash"
        ),
    )
    op.create_index(
        "ix_crisis_content_copy_versions_card_id",
        "crisis_content_copy_versions",
        ["card_id"],
    )

    op.create_table(
        "crisis_content_notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("card_id", sa.BigInteger(), nullable=False),
        sa.Column("route", sa.Text(), nullable=False),
        sa.Column("status_value", sa.Text(), nullable=False),
        sa.Column(
            "notified_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["crisis_content_cards.id"],
            name="fk_notifications_card",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "card_id",
            "route",
            "status_value",
            name="uq_crisis_content_notifications_card_route_status",
        ),
    )
    op.create_index(
        "ix_crisis_content_notifications_card_id",
        "crisis_content_notifications",
        ["card_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crisis_content_notifications_card_id", table_name="crisis_content_notifications"
    )
    op.drop_table("crisis_content_notifications")

    op.drop_index(
        "ix_crisis_content_copy_versions_card_id", table_name="crisis_content_copy_versions"
    )
    op.drop_table("crisis_content_copy_versions")

    op.execute(f"DROP INDEX {_IDENTITY_INDEX}")
    op.drop_table("crisis_content_cards")
