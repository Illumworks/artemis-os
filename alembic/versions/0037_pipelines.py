"""pipelines — pipeline data model + pipeline_runs tables (PIPE1).

Revision ID: 0037
Revises: 0036
Create Date: 2026-05-21

Two new tables:
  pipelines       — unified orchestration primitive (nodes + edges as JSONB)
  pipeline_runs   — per-execution run records for pipelines
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0037"
down_revision: str = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipelines",
        sa.Column("id", sa.Text, primary_key=True, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("nodes", JSONB, nullable=False, server_default="[]"),
        sa.Column("edges", JSONB, nullable=False, server_default="[]"),
        sa.Column("trigger_config", JSONB, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("owner_user_id", sa.BigInteger, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_pipelines_status",
        ),
    )
    op.create_index("idx_pipelines_status_owner", "pipelines", ["status", "owner_user_id"])
    op.create_index(
        "idx_pipelines_nodes_gin",
        "pipelines",
        ["nodes"],
        postgresql_using="gin",
    )
    op.create_index(
        "idx_pipelines_edges_gin",
        "pipelines",
        ["edges"],
        postgresql_using="gin",
    )

    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.Text, primary_key=True, nullable=False),
        sa.Column(
            "pipeline_id",
            sa.Text,
            sa.ForeignKey("pipelines.id", name="fk_pipeline_runs_pipeline", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("trigger", sa.Text, nullable=False, server_default="manual"),
        sa.Column("triggered_by", sa.Text, nullable=True),
        sa.Column("node_states", JSONB, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'awaiting_approval', 'succeeded', 'failed', 'cancelled')",
            name="ck_pipeline_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'webhook', 'event')",
            name="ck_pipeline_runs_trigger",
        ),
    )
    op.create_index(
        "idx_pipeline_runs_pipeline_started",
        "pipeline_runs",
        ["pipeline_id", sa.text("started_at DESC NULLS LAST")],
    )


def downgrade() -> None:
    op.drop_index("idx_pipeline_runs_pipeline_started", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
    op.drop_index("idx_pipelines_edges_gin", table_name="pipelines")
    op.drop_index("idx_pipelines_nodes_gin", table_name="pipelines")
    op.drop_index("idx_pipelines_status_owner", table_name="pipelines")
    op.drop_table("pipelines")
