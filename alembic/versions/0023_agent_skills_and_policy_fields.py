"""agent_skills_and_policy_fields — agent_skills join table + package policy columns on agents.

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-19

J11: Agents Operations parity.

- New table ``agent_skills``: many-to-many join between agents and skills,
  with a secondary index on skill_slug for reverse lookup.
- New columns on ``agents``:
  - fallback_provider TEXT NULL
  - fallback_model TEXT NULL
  - memory_policy TEXT NOT NULL DEFAULT 'session_scoped'
  - permission_mode TEXT NOT NULL DEFAULT 'ask'
  - output_contract JSONB NULL
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0023"
down_revision: str = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # New columns on agents
    op.add_column("agents", sa.Column("fallback_provider", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("fallback_model", sa.Text(), nullable=True))
    op.add_column(
        "agents",
        sa.Column(
            "memory_policy",
            sa.Text(),
            nullable=False,
            server_default="session_scoped",
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "permission_mode",
            sa.Text(),
            nullable=False,
            server_default="ask",
        ),
    )
    op.add_column("agents", sa.Column("output_contract", JSONB(), nullable=True))

    # New agent_skills join table
    op.create_table(
        "agent_skills",
        sa.Column("agent_id", sa.BigInteger(), nullable=False),
        sa.Column("skill_slug", sa.Text(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_agent_skills_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["skill_slug"],
            ["skills.slug"],
            name="fk_agent_skills_skill",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("agent_id", "skill_slug", name="pk_agent_skills"),
    )
    op.create_index(
        "ix_agent_skills_skill_slug",
        "agent_skills",
        ["skill_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_skills_skill_slug", table_name="agent_skills")
    op.drop_table("agent_skills")
    op.drop_column("agents", "output_contract")
    op.drop_column("agents", "permission_mode")
    op.drop_column("agents", "memory_policy")
    op.drop_column("agents", "fallback_model")
    op.drop_column("agents", "fallback_provider")
