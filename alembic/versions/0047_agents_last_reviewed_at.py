"""agents last_reviewed_at

Revision ID: 0047
Revises: 0046
Create Date: 2026-05-29

Changes:
  - agents.last_reviewed_at TIMESTAMPTZ nullable column added.
    Records when an operator last reviewed this agent's pending proposals /
    trajectory summaries via the Proposals Inbox surface.  NULL means never
    reviewed (always included in the inbox).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("last_reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "last_reviewed_at")
