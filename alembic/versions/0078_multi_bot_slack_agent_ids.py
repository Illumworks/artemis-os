"""Add agent_id to integrations for multi-bot Slack routing.

Revision ID: 0078_multi_bot_slack_agent_ids
Revises: 0077_signal_worklist_overrides
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0078_multi_bot_slack_agent_ids"
down_revision = "0077_signal_worklist_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "integrations",
        sa.Column("agent_id", sa.Text(), nullable=True, server_default=sa.text("'default'")),
    )
    op.execute("UPDATE integrations SET agent_id = 'artemis' WHERE provider = 'slack'")
    op.execute("UPDATE integrations SET agent_id = 'default' WHERE agent_id IS NULL")
    op.alter_column("integrations", "agent_id", nullable=False)
    # NOTE: the actual existing unique constraint is named uq_integrations_provider_workspace
    # (explicit name in the model), NOT the auto "<table>_<cols>_key" form.
    op.drop_constraint("uq_integrations_provider_workspace", "integrations", type_="unique")
    op.create_unique_constraint(
        "uq_integrations_provider_workspace_agent",
        "integrations",
        ["provider", "workspace_id", "agent_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_integrations_provider_workspace_agent",
        "integrations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_integrations_provider_workspace",
        "integrations",
        ["provider", "workspace_id"],
    )
    op.drop_column("integrations", "agent_id")
