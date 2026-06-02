"""CC22 — definition_proposals.rejection_reason + rejected_at.

Revision ID: 0051
Revises: 0050
Create Date: 2026-05-30

Changes:
  - definition_proposals.rejection_reason (Text, nullable) — optional WHY
    captured when an operator rejects a proposal.  Feeds back to operators
    (and eventually the Builder LLM) so rejection patterns are learnable.
  - definition_proposals.rejected_at (TIMESTAMPTZ, nullable) — set when the
    proposal flips to status='rejected'.  Existing rejected rows (1, 2, 3
    from yesterday) remain NULL — we don't backfill.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051"
down_revision: str = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "definition_proposals",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "definition_proposals",
        sa.Column("rejected_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("definition_proposals", "rejected_at")
    op.drop_column("definition_proposals", "rejection_reason")
