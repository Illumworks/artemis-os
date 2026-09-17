"""champions_items.parent_discussion_id — so "Replied by Amira" can be computed

Revision ID: 0127
Revises: 0126
Create Date: 2026-09-17

"Replied by Amira" is a property of the THREAD, not of the item: a discussion
counts as replied when any comment on it was written from an amiralearning.com
address. Items were stored without their parent, so the column existed and could
never be filled. Vanilla returns discussionID on every comment, so this is a
re-ingest away rather than a new API call.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0127"
down_revision = "0126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("champions_items", sa.Column("parent_discussion_id", sa.Integer(), nullable=True))
    op.create_index("idx_champions_items_parent", "champions_items", ["parent_discussion_id"])


def downgrade() -> None:
    op.drop_index("idx_champions_items_parent", table_name="champions_items")
    op.drop_column("champions_items", "parent_discussion_id")
