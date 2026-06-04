"""Phase 3 Piece B — writing_training_candidates storage substrate.

Creates the per-draft/per-profile training candidate table that backs the
Writing Studio learning-loop (propose/approve/reject review flow).

Node reference: db/sqlite.js writing_training_candidates (lines 616-632).
Adaptations for Postgres / Python rebuild:
  - draft_id FKs to campaign_deliverables.id (Python rebuild's draft row),
    not writing_drafts.id (which does not exist in this repo yet). Matches
    the same convention as writing_draft_thread_messages.
  - scope_json is JSONB (not TEXT as in SQLite).
  - All timestamps are TIMESTAMPTZ.

Revision ID: 0064
Revises: 0063
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0064"
down_revision: str = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "writing_training_candidates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("profile_id", sa.BigInteger(), nullable=True),
        sa.Column("draft_id", sa.BigInteger(), nullable=True),
        sa.Column("candidate_type", sa.Text(), server_default="rule", nullable=False),
        sa.Column("proposed_text", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="proposed", nullable=False),
        sa.Column("scope_json", JSONB(), nullable=True),
        sa.Column("source_version_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_memory_observation_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["writing_profiles.id"],
            name="fk_wtc_profile",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["campaign_deliverables.id"],
            name="fk_wtc_draft",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_writing_training_candidates_profile",
        "writing_training_candidates",
        ["profile_id"],
    )
    op.create_index(
        "idx_writing_training_candidates_status",
        "writing_training_candidates",
        ["status"],
    )
    op.create_index(
        "idx_writing_training_candidates_draft",
        "writing_training_candidates",
        ["draft_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_writing_training_candidates_draft",
        table_name="writing_training_candidates",
    )
    op.drop_index(
        "idx_writing_training_candidates_status",
        table_name="writing_training_candidates",
    )
    op.drop_index(
        "idx_writing_training_candidates_profile",
        table_name="writing_training_candidates",
    )
    op.drop_constraint("fk_wtc_draft", "writing_training_candidates", type_="foreignkey")
    op.drop_constraint("fk_wtc_profile", "writing_training_candidates", type_="foreignkey")
    op.drop_table("writing_training_candidates")
