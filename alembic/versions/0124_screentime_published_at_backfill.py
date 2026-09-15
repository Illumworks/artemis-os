"""screentime_signals — backfill published_at, which was NULL on every row

Revision ID: 0124
Revises: 0123
Create Date: 2026-09-15

`published_at` was NULL on all 1,742 stored screentime signals. Not sometimes —
every single one, for the life of the table.

The cause is a format mismatch, fixed alongside this in
`artemis/screentime/filters.py::_parse_dt`: RSS publication dates arrive as
RFC 2822 (``"Tue, 11 Apr 2023 07:00:00 GMT"``) and the parser only tried
``datetime.fromisoformat``, which cannot read that shape. It returned None every
time, so the column was written NULL every time.

The consequence reached a reader. Nothing in the system could tell how old an
article was, so a story first seen on 15 August could be re-ingested on
15 September and head that day's brief as news — which is what Jon clicked. The
corpus also holds articles from 2019 and 2023 carried as current signals.

The date was never lost, only unparsed: it has been sitting in
``raw->'finding'->'metadata'->>'published_at'`` the whole time. This fills the
column from there.

**`AT TIME ZONE 'UTC'` is load-bearing.** ``to_timestamp`` with this format
ignores the trailing "GMT" and resolves against the SERVER's zone, which on this
machine shifted every date five hours. Measured before and after: without it,
``Fri, 11 Jan 2019 08:00:00 GMT`` stored as 13:00 UTC.

Only NULL rows are touched, and only where the raw value matches the expected
shape, so a row with a real date keeps it and a malformed one stays NULL rather
than becoming a wrong date. The downgrade nulls the column again, which restores
the previous state exactly.
"""

from alembic import op

revision = "0124"
down_revision = "0123"
branch_labels = None
depends_on = None

#: Anchored to the RFC 2822 shape. A row whose raw value is anything else is left
#: alone: an absent date is honest, a guessed one is not.
_RFC_2822 = r"^[A-Za-z]{3}, [0-9]{1,2} [A-Za-z]{3} [0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE screentime_signals
           SET published_at = (
                 to_timestamp(
                   raw->'finding'->'metadata'->>'published_at',
                   'Dy, DD Mon YYYY HH24:MI:SS'
                 )::timestamp AT TIME ZONE 'UTC'
               )
         WHERE published_at IS NULL
           AND raw->'finding'->'metadata'->>'published_at' ~ '{_RFC_2822}'
        """
    )


def downgrade() -> None:
    # Every row was NULL before this ran, so nulling restores it exactly.
    op.execute("UPDATE screentime_signals SET published_at = NULL")
