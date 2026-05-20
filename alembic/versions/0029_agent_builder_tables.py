"""agent_builder_tables — builder sessions, definition proposals, trajectory summaries.

Revision ID: 0029
Revises: 0028
Create Date: 2026-05-19

Adds the three tables required by brief O1 — Agent-Builder + Self-Improvement:
  - builder_sessions          — in-flight conversational builder sessions
  - definition_proposals      — proposed definitions awaiting user approval
  - agent_run_trajectory_summaries — per-run one-line summaries for self-improvement input
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from alembic import op

revision: str = "0029"
down_revision: str = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── builder_sessions ──────────────────────────────────────────────────────
    op.create_table(
        "builder_sessions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("builder_kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("conversation", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("draft", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_builder_sessions_kind",
        "builder_sessions",
        "builder_kind IN ('agent', 'skill', 'workflow', 'automation')",
    )
    op.create_check_constraint(
        "ck_builder_sessions_status",
        "builder_sessions",
        "status IN ('active', 'committed', 'abandoned')",
    )
    op.create_index("idx_builder_sessions_status", "builder_sessions", ["status"])
    op.create_index("idx_builder_sessions_user_id", "builder_sessions", ["user_id"])
    op.create_index("idx_builder_sessions_kind", "builder_sessions", ["builder_kind"])

    # ── definition_proposals ─────────────────────────────────────────────────
    op.create_table(
        "definition_proposals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True, nullable=False),
        sa.Column("builder_session_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("proposed_by", sa.Text(), nullable=False),
        sa.Column("proposed_definition", JSONB(), nullable=False),
        sa.Column("citations", JSONB(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["builder_session_id"],
            ["builder_sessions.id"],
            name="fk_definition_proposals_session",
            ondelete="SET NULL",
        ),
    )
    op.create_check_constraint(
        "ck_definition_proposals_kind",
        "definition_proposals",
        "kind IN ('agent', 'skill', 'workflow', 'automation')",
    )
    op.create_check_constraint(
        "ck_definition_proposals_proposed_by",
        "definition_proposals",
        "proposed_by IN ('user', 'builder', 'self-improvement')",
    )
    op.create_check_constraint(
        "ck_definition_proposals_status",
        "definition_proposals",
        "status IN ('pending', 'approved', 'rejected', 'superseded')",
    )
    op.create_index(
        "idx_definition_proposals_status", "definition_proposals", ["status"]
    )
    op.create_index(
        "idx_definition_proposals_session_id",
        "definition_proposals",
        ["builder_session_id"],
    )
    op.create_index(
        "idx_definition_proposals_kind_target",
        "definition_proposals",
        ["kind", "target_id"],
    )

    # ── agent_run_trajectory_summaries ────────────────────────────────────────
    op.create_table(
        "agent_run_trajectory_summaries",
        sa.Column("run_id", sa.BigInteger(), primary_key=True, nullable=False),
        sa.Column("what_worked", sa.Text(), nullable=True),
        sa.Column("what_stalled", sa.Text(), nullable=True),
        sa.Column("what_was_missing", sa.Text(), nullable=True),
        sa.Column(
            "generated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_trajectory_summaries_run",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "idx_trajectory_summaries_generated_at",
        "agent_run_trajectory_summaries",
        ["generated_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_run_trajectory_summaries")
    op.drop_table("definition_proposals")
    op.drop_table("builder_sessions")
