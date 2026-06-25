"""Add forge_mode column to dev_sessions.

NULL / "read"  = read-only Phase-2 chat behavior (default).
"write"        = worktree-isolated Forge build session.

Forge Phase 3, chunk 3.1.

Revision ID: 0104
Revises: 0103
Create Date: 2026-06-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0104"
down_revision: str | None = "0103"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("dev_sessions", sa.Column("forge_mode", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dev_sessions", "forge_mode")
