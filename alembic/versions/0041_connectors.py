"""connectors — per-source credential management tables.

Revision ID: 0041
Revises: 0040
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # connectors table
    op.create_table(
        "connectors",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "credentials",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("idx_connectors_kind", "connectors", ["kind"])
    op.create_index("idx_connectors_status", "connectors", ["status"])

    # agent_connectors join table
    op.create_table(
        "agent_connectors",
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("connector_id", UUID(as_uuid=False), nullable=False),
        sa.Column("tool_namespace", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "agent_id", "connector_id", "tool_namespace",
            name="pk_agent_connectors",
        ),
        sa.UniqueConstraint(
            "agent_id", "connector_id", "tool_namespace",
            name="uq_agent_connectors_triple",
        ),
    )
    op.create_index("idx_agent_connectors_agent_id", "agent_connectors", ["agent_id"])
    op.create_index(
        "idx_agent_connectors_connector_id", "agent_connectors", ["connector_id"]
    )


def downgrade() -> None:
    op.drop_index("idx_agent_connectors_connector_id", "agent_connectors")
    op.drop_index("idx_agent_connectors_agent_id", "agent_connectors")
    op.drop_table("agent_connectors")
    op.drop_index("idx_connectors_status", "connectors")
    op.drop_index("idx_connectors_kind", "connectors")
    op.drop_table("connectors")
