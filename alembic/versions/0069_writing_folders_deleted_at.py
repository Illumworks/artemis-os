"""Add deleted_at to writing_folders — soft-delete tombstone.

When a user explicitly deletes a folder (via DELETE /api/writing-studio/folders/:id),
we stamp deleted_at = now() rather than hard-deleting the row.  This lets
backfill_campaign_folders skip candidates whose per-candidate folder was explicitly
deleted, so a deleted campaign-derived folder never silently re-spawns on the next
overview load.

  - User-created folders (campaign_id IS NULL): hard-deleted (row removed), no tombstone needed.
  - Campaign-derived folders (campaign_id IS NOT NULL): soft-deleted (deleted_at stamped).

list_folders() and get_folder_by_candidate() are updated to exclude rows where
deleted_at IS NOT NULL.

Revision ID: 0069
Revises: 0068
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TIMESTAMP

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "writing_folders",
        sa.Column(
            "deleted_at",
            TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )
    # Partial index for fast lookup of non-deleted folders.
    op.create_index(
        "idx_writing_folders_deleted_at",
        "writing_folders",
        ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_writing_folders_deleted_at", table_name="writing_folders")
    op.drop_column("writing_folders", "deleted_at")
