"""DIST1 — district entity + tier bands + pure classifier substrate.

Revision ID: 0054
Revises: 0053
Create Date: 2026-05-31
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054"
down_revision: str = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "district_tier_bands",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("min_enrollment", sa.Integer(), nullable=True),
        sa.Column("max_enrollment", sa.Integer(), nullable=True),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "tier IN ('D1', 'D2', 'D3', 'D4')",
            name="ck_district_tier_bands_tier",
        ),
        sa.UniqueConstraint("tier", name="uq_district_tier_bands_tier"),
    )

    op.create_table(
        "districts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("nces_id", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("enrollment", sa.Integer(), nullable=True),
        sa.Column("tier", sa.Text(), nullable=True),
        sa.Column("supported", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "on_skip_list",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "classification_source",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'unresolved'"),
        ),
        sa.Column("classified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "tier IS NULL OR tier IN ('D1', 'D2', 'D3', 'D4')",
            name="ck_districts_tier",
        ),
        sa.CheckConstraint(
            "classification_source IN ('nces', 'manual', 'unresolved')",
            name="ck_districts_classification_source",
        ),
        sa.UniqueConstraint("nces_id", name="uq_districts_nces_id"),
    )

    op.create_index("idx_districts_state", "districts", ["state"])
    op.create_index("idx_districts_tier", "districts", ["tier"])
    op.create_index("idx_districts_supported", "districts", ["supported"])

    district_tier_bands = sa.table(
        "district_tier_bands",
        sa.column("tier", sa.Text()),
        sa.column("min_enrollment", sa.Integer()),
        sa.column("max_enrollment", sa.Integer()),
        sa.column("display_order", sa.Integer()),
    )
    op.bulk_insert(
        district_tier_bands,
        [
            {
                "tier": "D1",
                "min_enrollment": 25000,
                "max_enrollment": None,
                "display_order": 1,
            },
            {
                "tier": "D2",
                "min_enrollment": 10000,
                "max_enrollment": 24999,
                "display_order": 2,
            },
            {
                "tier": "D3",
                "min_enrollment": 5000,
                "max_enrollment": 9999,
                "display_order": 3,
            },
            {
                "tier": "D4",
                "min_enrollment": None,
                "max_enrollment": 4999,
                "display_order": 4,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_districts_supported", table_name="districts")
    op.drop_index("idx_districts_tier", table_name="districts")
    op.drop_index("idx_districts_state", table_name="districts")
    op.drop_table("districts")
    op.drop_table("district_tier_bands")
