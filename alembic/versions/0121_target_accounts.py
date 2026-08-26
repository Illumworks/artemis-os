"""target_accounts — the new-business universe Josh actually sells into

Revision ID: 0121
Revises: 0120
Create Date: 2026-08-25

Backs C5. Josh's complaint (catch-up, 2026-08-25) is that Callie surfaced three
districts and two were existing customers -- she has no notion of a target
universe. This is that universe: 1,287 accounts he posted as a TSV.

Two shapes in the real data drove the key choice, both found by profiling the
live file rather than imagining one:

  * ``(state, normalized_name)`` is NOT unique. "Hempfield Area School District"
    and "Hempfield School District" are two different PA districts that
    normalize identically. The natural key is therefore the RAW
    ``(state, account_name)``; the normalized form is a matching aid only.

  * One account ("Community Independent School District", TX) normalizes to the
    EMPTY STRING because every token is a stopword. `normalized_name` is
    nullable for exactly this, and an empty value must never be used as a
    match key -- it would behave as a wildcard.

``district_id`` is nullable ON PURPOSE and will stay NULL for roughly a fifth of
rows. Salesforce account names and NCES district names diverge ("Sweetwater
Union School District" vs "Sweetwater Union High"), and a unique match against
an incomplete table is confidently wrong. Unmatched rows abstain and say so via
``match_method``, rather than being guessed into a district.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0121"
down_revision = "0120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "target_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Natural key: the raw name as Salesforce spells it, plus the state.
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("state", sa.String(8), nullable=False),
        # Matching aid only. NULL/empty when every token is a stopword; such a
        # value must never be used as a match key.
        sa.Column("normalized_name", sa.Text(), nullable=True),
        sa.Column("marketing_tier", sa.String(8), nullable=True),
        sa.Column("enrollment", sa.Integer(), nullable=True),
        sa.Column("sales_owner", sa.Text(), nullable=True),
        sa.Column("channel_partner", sa.Text(), nullable=True),
        # Carried from the file even though it is constant (0 for every row in
        # the 2026-08-25 list). The list is ALREADY filtered to non-customers,
        # so exclusion works by membership, not by reading this column -- but a
        # future list may not be pre-filtered, and silently assuming otherwise
        # is how the wrong districts get targeted.
        sa.Column("is_customer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_parent_account", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("district_id", sa.BigInteger(), nullable=True),
        # How district_id was arrived at, or why it was not:
        # exact | normalized | abstained_ambiguous | abstained_no_match | abstained_unnormalizable
        sa.Column("match_method", sa.String(32), nullable=True),
        # Provenance: which upload produced this row, so a re-import is auditable.
        sa.Column("source_file_id", sa.String(64), nullable=True),
        sa.Column("imported_by", sa.String(32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("state", "account_name", name="uq_target_accounts_state_name"),
        sa.ForeignKeyConstraint(["district_id"], ["districts.id"], ondelete="SET NULL"),
    )
    # The matching lookup: state + normalized name. Not unique -- see Hempfield.
    op.create_index("ix_target_accounts_match", "target_accounts", ["state", "normalized_name"])
    op.create_index("ix_target_accounts_district", "target_accounts", ["district_id"])


def downgrade() -> None:
    op.drop_index("ix_target_accounts_district", table_name="target_accounts")
    op.drop_index("ix_target_accounts_match", table_name="target_accounts")
    op.drop_table("target_accounts")
