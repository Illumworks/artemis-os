"""approvals — add pipe4_context JSONB column for PIPE4 gate rendering context.

Revision ID: 0045
Revises: 0044
Create Date: 2026-05-22

Changes:
  - approvals.pipe4_context JSONB nullable column added
    Populated by human_gate_executor when a gate fires inside a pipeline run.
    Contains: pipeline_run_id, pipeline_name, node_id, node_label, context dict.
    NULL for non-PIPE4 approvals — UI falls back to existing render path.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0045"
down_revision: str = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "approvals",
        sa.Column(
            "pipe4_context",
            JSONB,
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("approvals", "pipe4_context")
