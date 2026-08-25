"""Brand Signals corpus — brand_signal_findings.

Revision ID: 0119
Revises: 0118
Create Date: 2026-08-25

Context
-------
Brand Signals shipped stateless. Every run re-scanned the same 120-day window,
so each morning's brief re-listed the same ~30 stories, and the headline counts
drifted (32 stories / 4 naming Amira one night, 31 / 3 the next morning) purely
because Google News returns slightly different results per call. Nothing was
accumulating, which also meant the market-strategy corpus this feed was supposed
to build did not exist.

An audit of the other feeds confirmed this was NOT systemic: all nine scouts
persist to ``scout_runs`` + ``signal_queue`` (2,967 rows, fresh daily). Brand
Signals was the single outlier.

What this adds
--------------
``brand_signal_findings`` — one row per distinct story, kept forever (no
deletes; consistent with the repo's lossless rule). ``reported_at`` is the
"already briefed" marker that lets the daily post say what is NEW rather than
repeating the window.

``content_hash`` is the normalized TITLE, not the link: Google News links are
opaque redirect blobs and if Google regenerates one, the same story would land
as a new row every morning — the exact repetition this table exists to stop.
See ``artemis/sentiment/models.py`` for the full reasoning.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0119"
down_revision = "0118"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "brand_signal_findings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("link", sa.Text(), nullable=False),
        sa.Column("lane", sa.Text(), server_default="category", nullable=False),
        sa.Column("state", sa.Text(), server_default="US", nullable=False),
        sa.Column(
            "themes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("names_amira", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("published_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reported_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash"),
    )
    op.create_index("ix_brand_signal_findings_unreported", "brand_signal_findings", ["reported_at"])
    op.create_index("ix_brand_signal_findings_published", "brand_signal_findings", ["published_at"])
    op.create_index("ix_brand_signal_findings_state", "brand_signal_findings", ["state"])


def downgrade() -> None:
    op.drop_index("ix_brand_signal_findings_state", table_name="brand_signal_findings")
    op.drop_index("ix_brand_signal_findings_published", table_name="brand_signal_findings")
    op.drop_index("ix_brand_signal_findings_unreported", table_name="brand_signal_findings")
    op.drop_table("brand_signal_findings")
