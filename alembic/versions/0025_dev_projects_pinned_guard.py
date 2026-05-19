"""dev_projects pinned sessions guard.

Revision ID: 0025
Revises: 0024
Create Date: 2026-05-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE dev_sessions "
        "ADD COLUMN IF NOT EXISTS pinned boolean NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE dev_sessions DROP COLUMN IF EXISTS pinned")
