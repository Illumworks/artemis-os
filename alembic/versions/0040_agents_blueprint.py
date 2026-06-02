"""agents_blueprint — expose markdown operating blueprint fields.

Revision ID: 0040
Revises: 0039
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("cadence_seconds", sa.Integer(), nullable=True))
    op.add_column("agents", sa.Column("lifecycle_status", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("urgency_tiers", JSONB(), nullable=True))
    op.add_column("agents", sa.Column("failure_modes", JSONB(), nullable=True))
    op.add_column("agents", sa.Column("db_tables_touched", JSONB(), nullable=True))
    op.add_column("agents", sa.Column("implementation_notes", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("inputs_required", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "inputs_required")
    op.drop_column("agents", "implementation_notes")
    op.drop_column("agents", "db_tables_touched")
    op.drop_column("agents", "failure_modes")
    op.drop_column("agents", "urgency_tiers")
    op.drop_column("agents", "lifecycle_status")
    op.drop_column("agents", "cadence_seconds")
