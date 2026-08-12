"""Crisis-content: persist is_test on the card row (CCA13 follow-up).

Revision ID: 0111
Revises: 0110

CCA13 computes ``is_test`` (the card's live table lives on a tab whose title
carries the test marker) and puts it on the in-memory ``Transition``. That is
enough to route the notification to Jon's DM, but NOT enough to suppress the
vendor-facing side effects: those fire from the decision click, minutes or hours
later, in a code path that only has a ``card_id``.

Without this column, approving a card on the TESTING tab would have written a
Drive ``@mention`` and sent a Gmail to the external vendor about a post that
does not exist. The CCA13 worker flagged that gap rather than faking a test for
it, and this is the missing half.

Defaults False, so every pre-existing row is a real card -- which is correct:
the test tab did not exist when they were written.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0111"
down_revision: str | None = "0110"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "crisis_content_cards",
        sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("crisis_content_cards", "is_test")
