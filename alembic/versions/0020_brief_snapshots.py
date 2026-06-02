"""brief_snapshots — daily focus brief persistence.

Revision ID: 0020
Revises: 0019
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brief_snapshots",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("brief_json", postgresql.JSONB, nullable=False),
        sa.Column("sources_json", postgresql.JSONB, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("tokens_input", sa.Integer, nullable=True),
        sa.Column("tokens_output", sa.Integer, nullable=True),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_brief_snapshots_generated_at",
        "brief_snapshots",
        ["generated_at"],
        postgresql_using="btree",
        postgresql_ops={"generated_at": "DESC"},
    )


def downgrade() -> None:
    op.drop_index("ix_brief_snapshots_generated_at", table_name="brief_snapshots")
    op.drop_table("brief_snapshots")
