"""meeting_summaries + meeting_match_log — J6d auto-summarization tables.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-18

Phase J6d: Calendar-driven post-meeting auto-summary.
  - meeting_summaries: idempotent (UNIQUE on granola_id) store of LLM-generated
    summaries. raw_input_id FK links back to M1 hash chain.
  - meeting_match_log: append-only log of title-match attempts (hits and misses).
    Never deleted; provides debugging visibility into naming drift between GCal
    and Granola.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "meeting_summaries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("granola_id", sa.Text, nullable=False),
        sa.Column("gcal_event_id", sa.Text, nullable=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("action_items", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("raw_input_id", sa.BigInteger, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("granola_id", name="uq_meeting_summaries_granola_id"),
        sa.ForeignKeyConstraint(
            ["raw_input_id"],
            ["raw_inputs.id"],
            name="fk_meeting_summaries_raw_input",
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_meeting_summaries_created_at",
        "meeting_summaries",
        ["created_at"],
    )

    op.create_table(
        "meeting_match_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("gcal_event_id", sa.Text, nullable=True),
        sa.Column("gcal_title", sa.Text, nullable=False),
        sa.Column("gcal_end_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("matched_granola_id", sa.Text, nullable=True),
        sa.Column("match_kind", sa.Text, nullable=True),
        sa.Column("best_candidate_title", sa.Text, nullable=True),
        sa.Column("best_candidate_id", sa.Text, nullable=True),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column(
            "logged_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_meeting_match_log_logged_at",
        "meeting_match_log",
        ["logged_at"],
    )
    op.create_index(
        "ix_meeting_match_log_gcal_event_id",
        "meeting_match_log",
        ["gcal_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_meeting_match_log_gcal_event_id", table_name="meeting_match_log")
    op.drop_index("ix_meeting_match_log_logged_at", table_name="meeting_match_log")
    op.drop_table("meeting_match_log")

    op.drop_index("ix_meeting_summaries_created_at", table_name="meeting_summaries")
    op.drop_constraint("fk_meeting_summaries_raw_input", "meeting_summaries", type_="foreignkey")
    op.drop_table("meeting_summaries")
