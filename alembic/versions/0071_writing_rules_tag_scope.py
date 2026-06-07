"""Add tag_scope to writing_rules for scoped rule resolution.

Revision ID: 0071
Revises: 0070
Create Date: 2026-06-07
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE writing_rules
        ADD COLUMN tag_scope JSONB NOT NULL DEFAULT '{}'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE writing_rules
        DROP COLUMN tag_scope
    """)
