"""champions_items.post_type — Vanilla's own classification of a post

Revision ID: 0128
Revises: 0127
Create Date: 2026-09-17

Hannah's sheet carries a "Post Type" column -- Discussion, Tip, Inspiration --
and it is not something anyone derived. Vanilla classifies every post with a
`postTypeID` that the author chooses when posting, and exposes the display names
on /post-types. Storing the source's own answer beats asking a model to infer
one: the author's choice cannot be wrong about what they meant to write.

Null for comments, which have no post type of their own -- her sheet repeats
"Comment" there, which is the Type column restated.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0128"
down_revision = "0127"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("champions_items", sa.Column("post_type", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("champions_items", "post_type")
