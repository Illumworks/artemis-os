"""gong_call_window_cache — share the paged window across processes

Revision ID: 0123
Revises: 0122
Create Date: 2026-09-12

Gong offers no server-side account filter, so finding one district's calls means
paging the whole window and matching names locally — about 57 seconds. That was
cached in process, which helped a script and not the thing anyone uses: Callie's
tools run in `artemis.tools.mcp_server`, spawned PER TURN by the claude-code
adapter, so every turn started with an empty cache and paid the full cost. It is
why Josh waited 361 seconds on one question.

A cache, not a source of truth: `payload` is derived data that can be rebuilt by
asking Gong again, so it carries no foreign keys and nothing reads it as
authoritative. Keyed by window length because a 180-day corpus is not a 120-day
one.

No call CONTENT here — the same metadata `CallContext` already holds, which is
tracker counts, party counts and Salesforce account names. Private calls are
dropped before anything reaches this table (`drop_private`).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0123"
down_revision = "0122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gong_call_window_cache",
        sa.Column("days", sa.Integer(), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_table("gong_call_window_cache")
