"""WS Phase 2 piece ① — writing_draft_thread_messages storage substrate.

Creates the per-draft AI conversation thread table that backs the Writing
Studio "converse with the AI" feature (compose endpoint, Phase 2 piece ②).

Node reference: db/sqlite.js writing_draft_thread_messages table.
Adaptations for Postgres / Python rebuild:
  - draft_id FKs to campaign_deliverables.id (Python draft row), not
    writing_drafts.id (which does not exist in this repo yet).
  - created_at is TIMESTAMPTZ not a Unix integer.
  - Node's *_json TEXT columns become JSONB for native query support.

Revision ID: 0063
Revises: 0062
Create Date: 2026-06-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0063"
down_revision: str = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "writing_draft_thread_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("draft_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        # Node column name is "text"; keep it for semantic parity.
        sa.Column("text", sa.Text(), nullable=False),
        # Node's *_json TEXT columns → JSONB.
        sa.Column("attachments_json", JSONB(), nullable=True),
        sa.Column("trace_json", JSONB(), nullable=True),
        sa.Column("engine_json", JSONB(), nullable=True),
        sa.Column("prompt_json", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["campaign_deliverables.id"],
            name="fk_wdtm_draft",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # Composite index mirrors Node's idx_writing_thread_messages_draft:
    # chronological listing within a draft, id as tiebreaker.
    op.create_index(
        "idx_writing_thread_messages_draft",
        "writing_draft_thread_messages",
        ["draft_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_writing_thread_messages_draft",
        table_name="writing_draft_thread_messages",
    )
    op.drop_constraint(
        "fk_wdtm_draft",
        "writing_draft_thread_messages",
        type_="foreignkey",
    )
    op.drop_table("writing_draft_thread_messages")
