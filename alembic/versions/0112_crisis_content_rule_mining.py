"""Crisis-content: rule-mining observation log + pair aggregate (CCA15).

Revision ID: 0112
Revises: 0111

Two new, purely additive tables backing
``artemis.crisis_content.rule_mining`` -- turning repeated Google Docs
suggestions from Angela/Hannah's editing pass on the vendor's copy into
candidate ``writing_rules`` proposals for the existing human review queue.
Neither table is written to by any other module; nothing here touches
``writing_rules``, ``writing_examples``, ``writing_training_candidates``, or
any existing ``crisis_content_*`` table.

``crisis_content_rule_mining_observations`` -- append-only, one row per
    distinct suggestion occurrence (never UPDATEd/DELETEd -- CLAUDE.md rule
    3). Unique on ``occurrence_key`` so re-polling a still-pending
    suggestion cannot double-count it.

``crisis_content_rule_mining_pairs`` -- one row per normalized
    (deleted, inserted) pair, holding a running ``occurrence_count`` and a
    ``status`` that flips ``counting`` -> ``proposed`` exactly once, the
    moment a pair first reaches the mining threshold. This is what makes
    re-running idempotent: an already-proposed pair is never proposed again.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0112"
down_revision: str | None = "0111"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "crisis_content_rule_mining_observations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("occurrence_key", sa.Text(), nullable=False),
        sa.Column("normalized_deleted", sa.Text(), nullable=False),
        sa.Column("normalized_inserted", sa.Text(), nullable=False),
        sa.Column("deleted_text", sa.Text(), nullable=False),
        sa.Column("inserted_text", sa.Text(), nullable=False),
        sa.Column("tab_id", sa.Text(), nullable=True),
        sa.Column("tab_title", sa.Text(), nullable=True),
        sa.Column("card_header", sa.Text(), nullable=True),
        sa.Column(
            "observed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "occurrence_key", name="uq_crisis_content_rule_mining_observations_occurrence"
        ),
    )
    op.create_index(
        "ix_crisis_content_rule_mining_observations_pair",
        "crisis_content_rule_mining_observations",
        ["normalized_deleted", "normalized_inserted"],
    )

    op.create_table(
        "crisis_content_rule_mining_pairs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("normalized_deleted", sa.Text(), nullable=False),
        sa.Column("normalized_inserted", sa.Text(), nullable=False),
        sa.Column("display_deleted", sa.Text(), nullable=False),
        sa.Column("display_inserted", sa.Text(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="counting"),
        sa.Column("proposed_candidate_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "normalized_deleted",
            "normalized_inserted",
            name="uq_crisis_content_rule_mining_pairs_pair",
        ),
    )


def downgrade() -> None:
    op.drop_table("crisis_content_rule_mining_pairs")
    op.drop_index(
        "ix_crisis_content_rule_mining_observations_pair",
        table_name="crisis_content_rule_mining_observations",
    )
    op.drop_table("crisis_content_rule_mining_observations")
