"""DIST3 — add resolved_district_id FK to signal_queue (additive, lossless).

Legacy district_id (text) is preserved for provenance. The new
resolved_district_id is the canonical FK to the districts entity and is
set by the District Classifier agent after name-resolution.

Revision ID: 0055
Revises: 0054
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0055"
down_revision: str = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal_queue",
        sa.Column("resolved_district_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_signal_queue_resolved_district",
        "signal_queue",
        "districts",
        ["resolved_district_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_signal_queue_resolved_district",
        "signal_queue",
        ["resolved_district_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_signal_queue_resolved_district", table_name="signal_queue")
    op.drop_constraint(
        "fk_signal_queue_resolved_district",
        "signal_queue",
        type_="foreignkey",
    )
    op.drop_column("signal_queue", "resolved_district_id")
