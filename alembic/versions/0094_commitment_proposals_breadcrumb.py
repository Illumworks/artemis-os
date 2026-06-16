"""Commitment proposals digest breadcrumb table.

Stores the number->commitment_id mapping after sending a proposals digest to Jon
so the reply handler can deterministically resolve which items he picked.
TTL-keyed (expires_at); rows are never deleted (lossless audit invariant).

Revision ID: 0094
Revises: 0093
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "commitment_proposals_breadcrumbs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # Slack user-ID of the digest recipient (Jon).
        sa.Column("recipient_id", sa.Text(), nullable=False),
        # JSON mapping: {"1": 42, "2": 43, ...}  number-as-string -> commitment_id.
        sa.Column("commitment_map", JSONB(), nullable=False),
        # ISO-formatted proposal text as sent (for audit / re-display).
        sa.Column("proposal_text", sa.Text(), nullable=False),
        # TTL: set to now+48h; live means expires_at > now AND completed_at IS NULL.
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        # Set when Jon replies and the digest is resolved (approved/left as-is).
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_commitment_proposals_breadcrumbs_recipient",
        "commitment_proposals_breadcrumbs",
        ["recipient_id", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_commitment_proposals_breadcrumbs_recipient",
        table_name="commitment_proposals_breadcrumbs",
    )
    op.drop_table("commitment_proposals_breadcrumbs")
