"""SEND2-A — district_contacts table for outbound campaign sends.

Revision ID: 0060
Revises: 0059
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060"
down_revision: str = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "district_contacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("district_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False, server_default="manual"),
        sa.Column("external_id", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('manual','salesforce')",
            name="ck_district_contacts_source",
        ),
        sa.ForeignKeyConstraint(
            ["district_id"],
            ["districts.id"],
            name="fk_district_contacts_district",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Plain index: (district_id, active) for common filter queries
    op.create_index(
        "idx_district_contacts_district_active",
        "district_contacts",
        ["district_id", "active"],
    )

    # Partial unique index: deduplication only on active rows for (district_id, lower(email))
    op.create_index(
        "uq_district_contacts_district_email",
        "district_contacts",
        ["district_id", sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    # Partial unique index: Salesforce external_id uniqueness only when present
    op.create_index(
        "uq_district_contacts_source_external",
        "district_contacts",
        ["source", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_district_contacts_source_external",
        table_name="district_contacts",
    )
    op.drop_index(
        "uq_district_contacts_district_email",
        table_name="district_contacts",
    )
    op.drop_index(
        "idx_district_contacts_district_active",
        table_name="district_contacts",
    )
    op.drop_constraint(
        "fk_district_contacts_district",
        "district_contacts",
        type_="foreignkey",
    )
    op.drop_table("district_contacts")
