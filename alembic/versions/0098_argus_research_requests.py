"""Add argus_research_requests table for dispatch persistence + restart recovery.

Each dispatch_research call writes a pending row BEFORE firing the background
task. On completion the row moves to done; on repeated failure (>= 3 attempts)
it moves to failed and a fallback Slack message is posted.

On app startup, any pending rows with attempts < 3 are re-fired so a restart
mid-dig never silently drops the work.

Revision ID: 0098
Revises: 0097
Create Date: 2026-06-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "argus_research_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("district_key", sa.Text(), nullable=False),
        sa.Column("channel_id", sa.Text(), nullable=False),
        sa.Column("team_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("signal", JSONB(), nullable=True),
        sa.Column("triggering_signal_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_argus_research_requests_pending",
        "argus_research_requests",
        ["status", "attempts"],
    )
    op.create_index(
        "idx_argus_research_requests_district",
        "argus_research_requests",
        ["district_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_argus_research_requests_district", table_name="argus_research_requests")
    op.drop_index("idx_argus_research_requests_pending", table_name="argus_research_requests")
    op.drop_table("argus_research_requests")
