"""Add staged_updates JSONB column to okr_checkin_breadcrumbs.

Stores the list of {kr_id, progress, basis} items staged by stage_okr_updates
tool. NULL when no staged updates exist. Applied server-side on operator 'go'.

Revision ID: 0082
Revises: 0081
Create Date: 2026-06-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "okr_checkin_breadcrumbs",
        sa.Column(
            "staged_updates",
            postgresql.JSONB(),
            nullable=True,
            comment=(
                "Staged KR updates pending operator 'go'. "
                "List of {kr_id, progress, basis}. NULL when empty."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("okr_checkin_breadcrumbs", "staged_updates")
