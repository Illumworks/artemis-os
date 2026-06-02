"""CI4 — pipeline run target-candidate link for campaign deliverables.

Revision ID: 0059
Revises: 0058
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0059"
down_revision: str = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pipeline_runs",
        sa.Column("target_candidate_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pipeline_runs_target_candidate",
        "pipeline_runs",
        "campaign_candidates",
        ["target_candidate_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_pipeline_runs_target_candidate",
        "pipeline_runs",
        ["target_candidate_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_runs_target_candidate", table_name="pipeline_runs")
    op.drop_constraint("fk_pipeline_runs_target_candidate", "pipeline_runs", type_="foreignkey")
    op.drop_column("pipeline_runs", "target_candidate_id")
