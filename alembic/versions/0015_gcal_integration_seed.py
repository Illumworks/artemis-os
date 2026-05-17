"""gcal_events_cache — optional read-through cache for Google Calendar events.

Revision ID: 0015
Revises: 0012
Create Date: 2026-05-17

Phase J2: Google Calendar integration. Reuses existing integrations +
integration_configs tables for credentials. Adds a local cache so the
Today/Meetings dashboard cards (J2b) can render without hitting Google on
every page load.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS gcal_events_cache (
            id              BIGSERIAL PRIMARY KEY,
            calendar_id     TEXT NOT NULL,
            event_id        TEXT NOT NULL,
            summary         TEXT,
            start_at        TIMESTAMPTZ,
            end_at          TIMESTAMPTZ,
            attendees       JSONB NOT NULL DEFAULT '[]'::jsonb,
            description     TEXT,
            fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (calendar_id, event_id)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gcal_events_cache_range "
        "ON gcal_events_cache (start_at, end_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_gcal_events_cache_calendar "
        "ON gcal_events_cache (calendar_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS gcal_events_cache")
