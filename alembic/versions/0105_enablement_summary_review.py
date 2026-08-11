"""Enablement catalog enrichment: AI-drafted summaries + review state + facets.

All 416 catalog rows have no summary and 129 have no audience, so nearly every
Kai answer carries "Caveat: Needs verification -- the catalog records don't
include a summary." There is also no format field (Sara asked for a Google
Slides deck and got a PDF) and no grade range ("Reading Risk report: K-8 or
PK-8?" was unanswerable).

Owner decision (Jon, 2026-08-11): AI writes the summaries, Sara and Missy
review, and their feedback regenerates. The review state exists so an AI draft
is never presented as catalog fact -- Kai caveats anything still ai_draft.

summary_status:
  NULL                  no summary at all (the current state of every row)
  'ai_draft'            AI-written, NOT reviewed. Kai must caveat it.
  'enablement_verified' a human approved or edited it. Kai states it plainly.
  'needs_revision'      a reviewer sent it back; summary_feedback says why.

Revision ID: 0105
Revises: 0104
Create Date: 2026-08-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("enablement_assets", sa.Column("summary_status", sa.Text(), nullable=True))
    op.add_column("enablement_assets", sa.Column("summary_reviewed_by", sa.Text(), nullable=True))
    op.add_column(
        "enablement_assets",
        sa.Column("summary_reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("enablement_assets", sa.Column("summary_feedback", sa.Text(), nullable=True))
    op.add_column(
        "enablement_assets",
        sa.Column("summary_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Facets that produced the visible misses in the channel.
    op.add_column("enablement_assets", sa.Column("format", sa.Text(), nullable=True))
    op.add_column("enablement_assets", sa.Column("grade_range", sa.Text(), nullable=True))

    # The review queue reads "everything awaiting a human", so index the status.
    op.create_index(
        "idx_enablement_assets_summary_status",
        "enablement_assets",
        ["summary_status"],
    )
    op.create_index("idx_enablement_assets_format", "enablement_assets", ["format"])


def downgrade() -> None:
    op.drop_index("idx_enablement_assets_format", table_name="enablement_assets")
    op.drop_index("idx_enablement_assets_summary_status", table_name="enablement_assets")
    op.drop_column("enablement_assets", "grade_range")
    op.drop_column("enablement_assets", "format")
    op.drop_column("enablement_assets", "summary_generated_at")
    op.drop_column("enablement_assets", "summary_feedback")
    op.drop_column("enablement_assets", "summary_reviewed_at")
    op.drop_column("enablement_assets", "summary_reviewed_by")
    op.drop_column("enablement_assets", "summary_status")
