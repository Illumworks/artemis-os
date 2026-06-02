"""DIST5 — district_data_meta singleton: tracks freshness of the loaded NCES dataset.

A single-row table stamped by the loader after each successful bulk ingest.
The migration also back-fills the current 2024-25 load by reading the live
districts count.

Revision ID: 0056
Revises: 0055
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0056"
down_revision: str = "0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "district_data_meta",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("school_year", sa.Text(), nullable=False),
        sa.Column(
            "loaded_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_district_data_meta"),
    )

    # Back-fill a row for the current 2024-25 load. We read the live districts
    # count at migration time so the stamp is honest even if the row count
    # differs slightly from 13,403 (e.g. test DBs will differ).
    conn = op.get_bind()
    result = conn.execute(sa.text("SELECT COUNT(*) FROM districts"))
    row = result.fetchone()
    district_count: int = row[0] if row else 0

    if district_count > 0:
        conn.execute(
            sa.text(
                "INSERT INTO district_data_meta (source, school_year, loaded_at, row_count, updated_at) "
                "VALUES (:source, :school_year, now(), :row_count, now())"
            ),
            {
                "source": "NCES CCD via Urban Institute Education Data API",
                "school_year": "2024-25",
                "row_count": district_count,
            },
        )


def downgrade() -> None:
    op.drop_table("district_data_meta")
