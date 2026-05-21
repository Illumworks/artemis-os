"""op1_automations — automations registry + automation_runs tables.

Revision ID: 0035
Revises: 0034
Create Date: 2026-05-20

Two new tables:
  automations      — registry of automation definitions (scheduled, manual, webhook)
  automation_runs  — per-execution run records linked back to the target run
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0035"
down_revision: str = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automations",
        sa.Column("id", sa.Text, primary_key=True, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("trigger_type", sa.Text, nullable=False, server_default="manual"),
        sa.Column("schedule_config", JSONB, nullable=True),
        sa.Column("target_type", sa.Text, nullable=False),
        sa.Column("target_id", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column("provider", sa.Text, nullable=True),
        sa.Column("fallback_provider", sa.Text, nullable=True),
        sa.Column("fallback_model", sa.Text, nullable=True),
        sa.Column("approval_policy", JSONB, nullable=True),
        sa.Column("output_config", JSONB, nullable=True),
        sa.Column("metadata", JSONB, nullable=True),
        sa.Column("owner_user_id", sa.Text, nullable=True),
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
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_automations_status",
        ),
        sa.CheckConstraint(
            "trigger_type IN ('manual', 'scheduled', 'webhook')",
            name="ck_automations_trigger_type",
        ),
        sa.CheckConstraint(
            "target_type IN ('agent', 'workflow', 'chain', 'dag')",
            name="ck_automations_target_type",
        ),
    )
    op.create_index("idx_automations_status_owner", "automations", ["status", "owner_user_id"])

    op.create_table(
        "automation_runs",
        sa.Column("id", sa.Text, primary_key=True, nullable=False),
        sa.Column(
            "automation_id",
            sa.Text,
            sa.ForeignKey("automations.id", name="fk_automation_runs_automation", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.Text, nullable=False, server_default="queued"),
        sa.Column("trigger", sa.Text, nullable=False, server_default="manual"),
        sa.Column("triggered_by", sa.Text, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("target_run_id", sa.Text, nullable=True),
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
            name="ck_automation_runs_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('manual', 'scheduled', 'webhook')",
            name="ck_automation_runs_trigger",
        ),
    )
    op.create_index(
        "idx_automation_runs_automation_created",
        "automation_runs",
        ["automation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_automation_runs_automation_created", table_name="automation_runs")
    op.drop_table("automation_runs")
    op.drop_index("idx_automations_status_owner", table_name="automations")
    op.drop_table("automations")
