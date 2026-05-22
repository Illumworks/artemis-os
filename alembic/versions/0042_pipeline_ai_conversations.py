"""pipeline_ai_conversations — conversation history per pipeline for AI Assistant.

Revision ID: 0042
Revises: 0041
Create Date: 2026-05-22

One row per pipeline_id; conversation is a JSONB array of {role, content} dicts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0042"
down_revision: str = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipeline_ai_conversations",
        sa.Column("pipeline_id", sa.Text, primary_key=True, nullable=False),
        sa.Column(
            "conversation",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            ["pipelines.id"],
            name="fk_pipeline_ai_conv_pipeline",
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    op.drop_table("pipeline_ai_conversations")
