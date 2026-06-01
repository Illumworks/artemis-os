"""CI2 — persist pending campaign initiation proposals.

Revision ID: 0058
Revises: 0057
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0058"
down_revision: str = "0057"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "campaign_candidates",
        sa.Column(
            "initiation_proposal_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("campaign_candidates", "initiation_proposal_json")
