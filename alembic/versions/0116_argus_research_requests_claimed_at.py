"""Add claimed_at to argus_research_requests for atomic claim-based dispatch (ARGUS-1).

Revision ID: 0116
Revises: 0115
Create Date: 2026-08-12

Context: ``dispatch_research`` used to fire ``loop.create_task(...)`` inside the
MCP subprocess that serves a single Slack turn -- the subprocess exits the
moment the turn ends, killing the task mid-research. The only reason any
research ever completed was ``recover_pending_requests`` re-firing orphaned
``pending`` rows on the next app restart. ARGUS-1 makes the tool enqueue only
and adds a claimer (APScheduler interval job, in the long-lived app process)
that atomically claims rows via ``UPDATE ... WHERE id = (SELECT ... FOR UPDATE
SKIP LOCKED) RETURNING *``.

This column is the only thing that claim needs and the existing table
doesn't have: a timestamp recording when a row was last claimed, so a
``running`` row whose claim is older than
``settings.argus_claim_stale_minutes`` is recognized as orphaned (crashed
mid-research) and re-claimable, instead of parked at ``running`` forever.
Purely additive, nullable -- every pre-existing row (all of them terminal,
``done``/``failed`` by the time this migration runs) gets NULL, which is
correct: they were never claimed under this scheme and are not claim
candidates regardless (the claim query only ever matches
``status IN ('pending', 'running')``).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0116"
down_revision: str | None = "0115"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "argus_research_requests",
        sa.Column("claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("argus_research_requests", "claimed_at")
