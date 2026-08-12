"""Writing Studio: quality + copy_hash on writing_examples (CCA14 harvest).

Revision ID: 0113
Revises: 0112

**Numbering note**: this was originally authored as revision 0112. The
concurrently-running CCA15 slice (rule mining) claimed 0112 first on its own
branch (``alembic/versions/0112_crisis_content_rule_mining.py``, not present
in this worktree). Renumbered to 0113 with ``down_revision="0112"`` to match
the order the two will land in once both merge to main -- see the CCA14
report for the mechanics (this worktree's ``alembic upgrade head`` cannot
resolve past 0111 until CCA15's 0112 is actually present; that failure is
expected here, not a bug in this file).

CCA14 harvests approved crisis-content copy into ``writing_examples`` (see
``docs/crisis-content-approval-pipeline.md`` "Slice D" and
``briefs/cca14-harvest-approved-copy.md``). Two additive columns:

``quality`` -- defaults to ``'unrated'`` on every row (existing 7 included),
so retroactive curation (the design doc's optional post-hoc star-rating
signal) needs no future migration -- the column already exists and already
has a value on every row.

``copy_hash`` -- nullable; NULL on every pre-existing row (they are
reference/template material with no crisis-content origin). Populated only
by the harvest path, sha256 of the approved copy body (mirrors
``CrisisContentCard.copy_hash``). Paired with ``channel`` in a UNIQUE
constraint so a re-run of the harvest for the same decision cannot insert a
duplicate row -- see ``artemis.crisis_content.harvest``'s "Idempotency"
section. Two rows with ``copy_hash IS NULL`` never collide on this
constraint (Postgres treats NULL <> NULL), so the existing 7 rows are
unaffected.

Deliberately NOT touching the existing
``idx_writing_examples_profile_title_type`` unique constraint
(``profile_id``, ``title``, ``example_type``) -- that natural key predates
this slice and other code (``artemis.writing_rules.repository.
promote_training_candidate``) depends on its exact shape. The harvest path
disambiguates ``title`` per channel + copy hash instead of widening that
constraint.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0113"
down_revision: str | None = "0112"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "writing_examples",
        sa.Column("quality", sa.Text(), nullable=False, server_default="unrated"),
    )
    op.add_column(
        "writing_examples",
        sa.Column("copy_hash", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_writing_examples_copy_hash_channel",
        "writing_examples",
        ["copy_hash", "channel"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_writing_examples_copy_hash_channel", "writing_examples", type_="unique"
    )
    op.drop_column("writing_examples", "copy_hash")
    op.drop_column("writing_examples", "quality")
