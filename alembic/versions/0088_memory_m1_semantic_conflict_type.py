"""Extend memory_conflicts.conflict_type to allow 'semantic_contradiction'.

M1 semantic conflict detector writes conflict rows with type='semantic_contradiction'
to distinguish LLM-judged paraphrased contradictions from rule-based detectors.
The existing ck_conflict_type constraint must be relaxed to include this new value.

Revision ID: 0088
Revises: 0087
Create Date: 2026-06-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Drop the existing check constraint and recreate it with the new value.
    # PostgreSQL does not support ALTER CHECK CONSTRAINT directly.
    op.drop_constraint("ck_conflict_type", "memory_conflicts", type_="check")
    op.create_check_constraint(
        "ck_conflict_type",
        "memory_conflicts",
        "conflict_type IN ("
        "'incompatible_values', "
        "'incompatible_temporal', "
        "'incompatible_relational', "
        "'semantic_contradiction'"
        ")",
    )


def downgrade() -> None:
    # Revert: drop semantic_contradiction value and restore original constraint.
    # Note: existing rows with semantic_contradiction will violate this; the
    # downgrade will fail if such rows exist. That is intentional — migration
    # is non-destructive upward but lossy downward.
    op.drop_constraint("ck_conflict_type", "memory_conflicts", type_="check")
    op.create_check_constraint(
        "ck_conflict_type",
        "memory_conflicts",
        "conflict_type IN ("
        "'incompatible_values', "
        "'incompatible_temporal', "
        "'incompatible_relational'"
        ")",
    )
