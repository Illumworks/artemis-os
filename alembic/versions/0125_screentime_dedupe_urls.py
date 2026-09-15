"""screentime_signals — one row per source URL

Revision ID: 0125
Revises: 0124
Create Date: 2026-09-15

`content_hash` is ``source_type|source_url|title``, and Google News does not keep
titles stable. The same Capitol News Illinois article arrived on 2026-08-15
suffixed " - capitolnewsillinois.com" and on 2026-09-15 as " - Capitol News
Illinois": different title, different hash, no conflict, second row. Jon clicked
the September copy and reached a month-old story.

**515 URLs carried more than one row, up to five apiece — 556 rows in total, 32%
of the table.** Every one is `national_news`, and a Google News redirect
addresses exactly one article, so these are copies rather than distinct items.

This keeps the EARLIEST row per URL — the first time we actually saw the story —
and removes the later copies. `store_signal` now rejects a URL it already holds,
so the condition does not recur; this clears what accumulated before that.

Three things were checked on the live table before this was written:

- **Nothing references these rows.** `screentime_signals` has no inbound foreign
  keys, so no child row is orphaned.
- **Nothing will be re-reported.** Callie's "already reported" markers live in
  `memory_observations` and name signal ids. Of the 105 affected URLs whose
  duplicate carried a marker, the kept row carries one too in every single case,
  so no story can reappear in a brief because its marker was on the copy.
- **The arithmetic closes.** 1,742 rows, 1,186 distinct URLs, 556 removed,
  1,186 remaining — exactly one per URL.

**The downgrade cannot restore these rows**, so the 556 were exported first to
`.backups/screentime_duplicates_<timestamp>.json` on the host that ran this
(gitignored — it holds article text). Recovery is a manual reload from that file.
On a database with no duplicates this is a no-op.
"""

from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # (discovered_at, id) as the ordering key: discovered_at alone ties when a
    # run stores several rows in the same instant, and a tie would make "the
    # earliest" ambiguous and the delete non-deterministic.
    op.execute(
        """
        DELETE FROM screentime_signals t
         WHERE t.source_url IS NOT NULL
           AND t.source_url <> ''
           AND EXISTS (
                 SELECT 1
                   FROM screentime_signals k
                  WHERE k.source_url = t.source_url
                    AND (k.discovered_at, k.id) < (t.discovered_at, t.id)
               )
        """
    )


def downgrade() -> None:
    """Irreversible by design — see the module docstring for the backup path."""
