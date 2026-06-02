"""signal_queue — link signals to pipeline_runs.

Revision ID: 0044
Revises: 0043
Create Date: 2026-05-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("signal_queue", sa.Column("pipeline_run_id", sa.Text(), nullable=True))
    op.create_foreign_key(
        "fk_signal_queue_pipeline_run", "signal_queue", "pipeline_runs",
        ["pipeline_run_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("idx_signal_queue_pipeline_run", "signal_queue", ["pipeline_run_id"])


def downgrade() -> None:
    op.drop_index("idx_signal_queue_pipeline_run", table_name="signal_queue")
    op.drop_constraint("fk_signal_queue_pipeline_run", "signal_queue", type_="foreignkey")
    op.drop_column("signal_queue", "pipeline_run_id")
