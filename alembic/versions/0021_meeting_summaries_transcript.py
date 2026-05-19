"""meeting_summaries — add transcript column (J6e).

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-19

J6e: persist full meeting transcript alongside the LLM-generated summary so
the Past tab can load instantly from local DB without re-hitting Granola.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meeting_summaries",
        sa.Column("transcript", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("meeting_summaries", "transcript")
