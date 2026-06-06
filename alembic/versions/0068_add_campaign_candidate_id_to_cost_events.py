"""Add nullable campaign_candidate_id to cost_events.

Additive, lossless change for the campaign-cost-rollup feature: campaign-tied
call sites (brief assembly, content drafting, sends) tag each cost row with
the campaign_candidate_id they belong to, enabling per-campaign cost rollup.

Historical rows stay NULL — they predate attribution, so they're correctly
"unattributed." Backward-compatible: every existing caller of
record_cost_event() works unchanged because the new param defaults to None.

Revision ID: 0068
Revises: 0067
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068"
down_revision: str = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cost_events",
        sa.Column("campaign_candidate_id", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_cost_events_campaign_candidate_id",
        "cost_events",
        ["campaign_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cost_events_campaign_candidate_id", table_name="cost_events")
    op.drop_column("cost_events", "campaign_candidate_id")
