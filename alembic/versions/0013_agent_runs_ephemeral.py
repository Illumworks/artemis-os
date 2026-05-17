"""agent_runs_ephemeral — add is_ephemeral flag to agent_runs.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-17

Phase G3-pre: spawn_subagent creates agent_runs rows with is_ephemeral=True
so ephemeral helper runs are cost-tracked but don't pollute the agents-page
run list by default. The agents-page list endpoint filters them out unless
?includeEphemeral=true is passed.
"""

# Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS is_ephemeral BOOL NOT NULL DEFAULT FALSE
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_ephemeral "
        "ON agent_runs (is_ephemeral) WHERE is_ephemeral = TRUE"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_ephemeral")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS is_ephemeral")
