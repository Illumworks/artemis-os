"""Add okr_checkin_breadcrumbs table for reconcile context injection.

Revision ID: 0081
Revises: 0080
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "okr_checkin_breadcrumbs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # Slack user ID of the recipient (e.g. "U09F3EPJXSQ")
        sa.Column("recipient_id", sa.Text(), nullable=False),
        # Serialised KR snapshot: list of {kr_id, kr_title, objective_title, prog, target_text}
        sa.Column("kr_snapshot", postgresql.JSONB(), nullable=False),
        # The full check-in proposal text as delivered (informational)
        sa.Column("proposal_text", sa.Text(), nullable=False),
        # When this breadcrumb expires (soft limit — TTL end of following Monday)
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        # Set when Jon's word-dump reconcile completes (layer-3 applied or declined)
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_okr_checkin_breadcrumbs_live",
        "okr_checkin_breadcrumbs",
        ["recipient_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_okr_checkin_breadcrumbs_live", table_name="okr_checkin_breadcrumbs")
    op.drop_table("okr_checkin_breadcrumbs")
