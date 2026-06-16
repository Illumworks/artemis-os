"""Opt-in commitments: add proposed status + commitment_decisions table.

Phase 1 of the opt-in commitments design:
- Expand commitments.status to include 'proposed' (drop+recreate named constraint).
- Add append-only commitment_decisions table for approve/dismiss learning signal.

Revision ID: 0093
Revises: 0092
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0093"
down_revision: str | None = "0092"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── 1. Expand commitments.status to include 'proposed' ───────────────────
    # The existing constraint only allows: active, snoozed, done, dismissed
    # (added by later migrations on top of the original 0083 constraint which
    # only had active, snoozed, done).  Drop+recreate to add 'proposed'.
    op.drop_constraint("ck_commitments_status", "commitments", type_="check")
    op.create_check_constraint(
        "ck_commitments_status",
        "commitments",
        "status IN ('proposed', 'active', 'snoozed', 'done', 'dismissed')",
    )

    # ── 2. commitment_decisions — append-only learning-signal table ───────────
    op.create_table(
        "commitment_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("commitment_id", sa.BigInteger(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column(
            "features",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "decided_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'dismiss')",
            name="ck_commitment_decisions_decision",
        ),
        sa.ForeignKeyConstraint(
            ["commitment_id"],
            ["commitments.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_commitment_decisions_commitment_id",
        "commitment_decisions",
        ["commitment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_commitment_decisions_commitment_id", table_name="commitment_decisions")
    op.drop_table("commitment_decisions")

    # Restore the pre-0093 constraint (without 'proposed')
    op.drop_constraint("ck_commitments_status", "commitments", type_="check")
    op.create_check_constraint(
        "ck_commitments_status",
        "commitments",
        "status IN ('active', 'snoozed', 'done', 'dismissed')",
    )
