"""integration_refresh_metadata — add last_refresh_attempt_at column.

Revision ID: 0027
Revises: 0026
Create Date: 2026-05-19

J10e: proactive OAuth token refresh. Adds a timestamp the scheduler uses as a
cooldown guard so two ticks (or a restart-mid-tick) don't both try to refresh
the same row. Plaintext — it's not a secret.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("last_refresh_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("integrations", "last_refresh_attempt_at")
