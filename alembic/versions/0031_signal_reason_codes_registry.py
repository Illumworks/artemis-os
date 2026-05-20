"""signal_reason_codes_registry — reason-code registry table + append-only trigger.

Revision ID: 0031
Revises: 0030
Create Date: 2026-05-20

Creates signal_reason_codes (code PK, domain, description, what_scout_looks_for,
default_urgency, is_active, created_at, updated_at) with an append-only DELETE
trigger (soft-delete via is_active = false only).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "signal_reason_codes",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("what_scout_looks_for", sa.Text(), nullable=True),
        sa.Column("default_urgency", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        sa.PrimaryKeyConstraint("code", name="pk_signal_reason_codes"),
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION signal_reason_codes_no_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'signal_reason_codes is append-only — soft-delete via is_active = false';
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    op.execute(
        """
        CREATE TRIGGER signal_reason_codes_block_delete
            BEFORE DELETE ON signal_reason_codes
            FOR EACH ROW EXECUTE FUNCTION signal_reason_codes_no_delete();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS signal_reason_codes_block_delete ON signal_reason_codes")
    op.execute("DROP FUNCTION IF EXISTS signal_reason_codes_no_delete()")
    op.drop_table("signal_reason_codes")
