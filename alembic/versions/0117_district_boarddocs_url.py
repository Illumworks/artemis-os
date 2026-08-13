"""ARGUS-2 -- districts.boarddocs_url + verified back-fill from peer_scout.

Revision ID: 0117
Revises: 0116
Create Date: 2026-08-13

Context: Argus's three weakest research dimensions (current_vendor,
decision_makers, competitor_commitments) are fed by board_minutes, which
returns 0 items for essentially every district because
``_fetch_board_minutes`` (``artemis/argus/research.py``) needs a
``boarddocs_url`` and only ever looked for one on the triggering signal.
Signals never carry one, so it always logged "no boarddocs_url -- skipping"
and returned ``[]``.

The URLs already existed: ``artemis/scouts/board_minutes/peer_scout.py``
hardcodes 27 of them, keyed by a hand-written id ("TX_dallas") that is a
different key space from ``districts.id`` -- nothing joined the two. This is
that join.

What this migration does
-------------------------
1. Adds ``districts.boarddocs_url`` (nullable ``TEXT``). This is a normal,
   PERMANENT NULL for most of the table's 13,466 rows -- BoardDocs coverage
   is a small, hand-curated watch list (peer_scout's own docstring: ~500-800
   districts eventually, 27 today), not something every district will ever
   have. NULL here means "no known BoardDocs URL", not "not yet checked".
2. Back-fills 26 of peer_scout's 27 watch-list entries by UPDATEing the
   matching ``districts`` row, using the hand-verified mapping in
   ``artemis.argus.board_minutes_backfill.BOARD_MINUTES_BACKFILL`` (imported
   here rather than duplicated -- see that module's docstring for the full
   per-row verification notes and match basis). Each UPDATE matches on
   ``id AND name = expected_name``: ``id`` is the real key, but the name
   check makes a stale/incorrect mapping fail safe (a no-op) rather than
   silently attaching a URL to a row that has since changed.
3. Deliberately leaves 1 of 27 (``OH_cleveland``) unmapped -- ``districts``
   has four Ohio rows containing "cleveland" and none is an exact match to
   peer_scout's "Cleveland Metropolitan School District"; see
   ``BOARD_MINUTES_UNMAPPED`` in the backfill module for why disambiguating
   it would require guessing rather than matching.

``peer_scout.py`` itself is untouched -- it keeps working exactly as it does
today (a live scout); this migration only adds a way for OTHER code
(``_fetch_board_minutes``) to find the same URLs it already has.

This is data-only for ``artemis_test_a`` and any other test database that
doesn't carry the full NCES district load (the UPDATE statements below match
zero rows there -- ``districts`` is normally empty in test DBs, populated
only in ``artemis_os`` via the separate NCES bulk-load path). That's
expected and harmless: the column still gets added, which is what the
runtime code and this migration's own tests depend on.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from artemis.argus.board_minutes_backfill import BOARD_MINUTES_BACKFILL

revision: str = "0117"
down_revision: str | None = "0116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "districts",
        sa.Column("boarddocs_url", sa.Text(), nullable=True),
    )

    conn = op.get_bind()
    for entry in BOARD_MINUTES_BACKFILL:
        conn.execute(
            sa.text(
                "UPDATE districts SET boarddocs_url = :url "
                "WHERE id = :id AND name = :expected_name"
            ),
            {
                "url": entry.boarddocs_url,
                "id": entry.districts_id,
                "expected_name": entry.expected_name,
            },
        )


def downgrade() -> None:
    op.drop_column("districts", "boarddocs_url")
