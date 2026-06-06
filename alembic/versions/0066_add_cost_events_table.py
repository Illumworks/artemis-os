"""Add cost_events table — unified LLM call cost ledger.

Provides a per-call append-only cost log across all features (agent runs,
Floating Artemis, workflows, memory, marketing, etc.). Replaces the fragmented
token fields on agent_runs / floating_artemis_messages / workflow_runs as the
source of truth for the Cost dashboard.

Lossless invariant: NO DELETE on this table, ever. Errored calls write rows
with is_error=True. Rate snapshot columns are frozen at write time.

Revision ID: 0066
Revises: 0065
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0066"
down_revision: str = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cost_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Provider attribution
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("provider_path", sa.Text(), nullable=False),
        # Feature attribution
        sa.Column("feature_tag", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=True),
        sa.Column("source_id", sa.Text(), nullable=True),
        # Context IDs
        sa.Column("agent_id", sa.BigInteger(), nullable=True),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("workflow_run_id", sa.BigInteger(), nullable=True),
        # Token counts
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "cache_creation_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cache_read_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        # Rate snapshot — frozen at write time
        sa.Column("input_rate_per_million", sa.Float(), nullable=False),
        sa.Column("output_rate_per_million", sa.Float(), nullable=False),
        sa.Column(
            "cache_write_rate_per_million",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "cache_read_rate_per_million",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
        # Computed cost
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        # Optional context
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("is_error", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("error_kind", sa.Text(), nullable=True),
    )

    # Single-column indexes for time-series queries and ID lookups
    op.create_index("ix_cost_events_created_at", "cost_events", ["created_at"])
    op.create_index("ix_cost_events_feature_tag", "cost_events", ["feature_tag"])
    op.create_index("ix_cost_events_agent_id", "cost_events", ["agent_id"])
    op.create_index("ix_cost_events_session_id", "cost_events", ["session_id"])
    op.create_index("ix_cost_events_workflow_run_id", "cost_events", ["workflow_run_id"])

    # Composite indexes for dashboard queries
    op.create_index(
        "idx_cost_events_created_feature",
        "cost_events",
        ["created_at", "feature_tag"],
    )
    op.create_index(
        "idx_cost_events_provider_model",
        "cost_events",
        ["provider", "model"],
    )


def downgrade() -> None:
    op.drop_index("idx_cost_events_provider_model", table_name="cost_events")
    op.drop_index("idx_cost_events_created_feature", table_name="cost_events")
    op.drop_index("ix_cost_events_workflow_run_id", table_name="cost_events")
    op.drop_index("ix_cost_events_session_id", table_name="cost_events")
    op.drop_index("ix_cost_events_agent_id", table_name="cost_events")
    op.drop_index("ix_cost_events_feature_tag", table_name="cost_events")
    op.drop_index("ix_cost_events_created_at", table_name="cost_events")
    op.drop_table("cost_events")
