"""agent_persona_jsonb — adds persona JSONB column to agents table.

Revision ID: 0030
Revises: 0029
Create Date: 2026-05-20

Adds agents.persona JSONB column (nullable) for the O2/O3 Agent Card +
persona/soul feature.

Persona shape:
  {
    "name": "Iris",
    "purpose": "Watches my Jira board and brings morning insight",
    "voice_notes": "lowercase, concise, no greetings",
    "ghostwrite": true,
    "profile_image_path": "/uploads/agents/{agent_id}/avatar.png" | null
  }

Agents without persona render with a (no persona set) placeholder in the
catalog and Agent Card surfaces.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0030"
down_revision: str = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("persona", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "persona")
