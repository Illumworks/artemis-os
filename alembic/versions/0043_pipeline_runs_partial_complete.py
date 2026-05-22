"""pipeline_runs — add partial_complete status + cost_usd column.

Revision ID: 0043
Revises: 0042
Create Date: 2026-05-22

Changes:
  - pipeline_runs.status constraint extended with 'partial_complete'
  - pipeline_runs.cost_usd column added (cumulative LLM cost for run)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0043"
down_revision: str = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop old check constraint
    op.drop_constraint("ck_pipeline_runs_status", "pipeline_runs")
    # Add new check constraint including partial_complete
    op.create_check_constraint(
        "ck_pipeline_runs_status",
        "pipeline_runs",
        "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', 'cancelled', 'partial_complete')",
    )
    # Add cost_usd column
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "cost_usd",
            sa.Float,
            nullable=False,
            server_default="0.0",
        ),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "cost_usd")
    op.drop_constraint("ck_pipeline_runs_status", "pipeline_runs")
    op.create_check_constraint(
        "ck_pipeline_runs_status",
        "pipeline_runs",
        "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', 'cancelled')",
    )
