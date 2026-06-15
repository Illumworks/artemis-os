"""P5 learning loop: add usage_count + last_used_at to skills table.

usage_count   — incremented each time a skill's instructions are injected
                into an agent run's system prompt.
last_used_at  — TIMESTAMPTZ, updated on each injection; NULL = never used.

Revision ID: 0091
Revises: 0090
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "skills",
        sa.Column(
            "usage_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "skills",
        sa.Column(
            "last_used_at",
            TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("skills", "last_used_at")
    op.drop_column("skills", "usage_count")
