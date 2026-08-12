"""Crisis-content rule mining: clear rows produced by the run-level extractor (CCA16).

Revision ID: 0114
Revises: 0113

CCA15 shipped mining at the Google Docs *run* level (a maximal consecutive
DEL-only textRun stretch paired with the ADD-only stretch immediately
following it). Its first live pass against Jen's real doc extracted six
(deleted, inserted) pairs; four of them were fragments of a single sentence
Angela rewrote in place, sliced wherever Google's diff happened to put a run
boundary, and one was a whole-paragraph deletion incorrectly paired with an
empty adjacent insertion run. Only one of the six was a genuine editorial
decision. See ``briefs/cca16-mine-spans-not-fragments.md`` and the CCA16
revision of ``artemis/crisis_content/rule_mining.py``, which replaces
run-level pairing with span-level coalescing so this cannot recur.

**Why deleting these six rows (and their occurrence rows) is not a CLAUDE.md
rule 3 (lossless memory) violation.** Rule 3 protects drawers and
observations -- durable evidence extracted from something that cannot be
re-derived. ``crisis_content_rule_mining_observations`` and
``crisis_content_rule_mining_pairs`` are neither: they are a *derived
aggregate* computed by an extractor this same migration's sibling code
change retires. The underlying evidence -- the vendor's suggestions
themselves -- lives in Jen's Google Doc, not in these tables, and is
re-readable on the next mining pass (mining is currently disabled via
``ARTEMIS_CRISIS_CONTENT_RULE_MINING_INTERVAL_MINUTES=0`` pending this fix,
per the brief). Keeping the six run-level rows would double-count every
underlying suggestion once it is re-mined correctly at span level: the same
physical edit would sit in the aggregate table twice, under two different
(and for four of the six, wrong) normalized keys. None of the six rows had
reached the proposal threshold (all had ``occurrence_count = 1``), so no
``writing_training_candidates`` row exists downstream of them -- this
migration touches nothing outside these two tables.

**Downgrade is a deliberate no-op, not a restore.** The deleted rows are
production data with no other durable copy in this database; there is
nothing to reconstruct them from inside a migration. Downgrading past this
revision leaves the two tables empty (their post-upgrade state), which is
survivable: both refill from the doc on the next mining pass. This is
called out explicitly rather than silently -- see the brief's "if you
believe deletion is wrong here, say so ... do not half-do it" instruction;
this worker's judgment is that deletion is correct for the reasons above.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0114"
down_revision: str | None = "0113"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # No FK relationship between these two tables (`proposed_candidate_id`
    # is a soft, unenforced reference into `writing_training_candidates`,
    # per `rule_mining_orm.py`) and neither is referenced by any other
    # table, so order between the two deletes below does not matter.
    op.execute(sa.text("DELETE FROM crisis_content_rule_mining_observations"))
    op.execute(sa.text("DELETE FROM crisis_content_rule_mining_pairs"))


def downgrade() -> None:
    """Deliberate no-op -- see the module docstring's "Downgrade" section.

    The six rows this revision deletes are production data mined from a
    now-superseded run-level extractor; there is no copy of them left to
    restore from. Downgrading past 0114 simply leaves both tables in the
    empty state upgrade() put them in.
    """
