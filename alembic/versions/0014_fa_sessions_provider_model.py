"""fa_sessions_provider_model — add provider + model columns to floating_artemis_sessions.

Revision ID: 0014
Revises: 0012
Create Date: 2026-05-17

Note: Revision 0013 is reserved for Lane B (agent_runs_ephemeral). When 0013
lands on main this migration's down_revision should be updated to "0013".

Allows floating-Artemis sessions to target a specific provider/model instead of
always defaulting to Anthropic. Both columns are nullable: NULL means "use the
Anthropic default" so all existing rows continue to work without touching them.
"""

# Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE floating_artemis_sessions
        ADD COLUMN IF NOT EXISTS provider TEXT,
        ADD COLUMN IF NOT EXISTS model TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE floating_artemis_sessions
        DROP COLUMN IF EXISTS provider,
        DROP COLUMN IF EXISTS model
    """)
