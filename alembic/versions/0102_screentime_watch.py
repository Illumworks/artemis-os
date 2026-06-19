"""Screen-Time Watch — isolated national policy-intelligence tables.

Three tables in the dedicated ``screentime_*`` namespace, disjoint from the
marketing SignalQueue / campaign tables and from memory:

  screentime_signals        — one row per discovered "real move"
                              (content_hash UNIQUE = dedup key).
  screentime_state_stance   — per-state heat-map rollup (state = PK).
  screentime_stance_config  — tunable stance rules (name = PK; 'default' is live).

Brief 1 (Screen-Time Watch #1). Migration number 0102 claimed in COORDINATION.md.

Revision ID: 0102
Revises: 0101
Create Date: 2026-06-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0102"
down_revision: str | None = "0101"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "screentime_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False, server_default="state"),
        sa.Column("district_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="news"),
        sa.Column("stance", sa.Text(), nullable=False, server_default="neutral"),
        sa.Column("amira_angle", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="regional_news"),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "discovered_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("is_real_move", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("raw", JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_screentime_signals_content_hash"),
    )
    op.create_index(
        "idx_screentime_signals_state_stance",
        "screentime_signals",
        ["state", "stance"],
    )
    op.create_index(
        "idx_screentime_signals_discovered",
        "screentime_signals",
        ["discovered_at"],
    )

    op.create_table(
        "screentime_state_stance",
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("stance", sa.Text(), nullable=False, server_default="no_info"),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("signal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_updated",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("state"),
    )

    op.create_table(
        "screentime_stance_config",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("rules", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("screentime_stance_config")
    op.drop_table("screentime_state_stance")
    op.drop_index("idx_screentime_signals_discovered", table_name="screentime_signals")
    op.drop_index("idx_screentime_signals_state_stance", table_name="screentime_signals")
    op.drop_table("screentime_signals")
