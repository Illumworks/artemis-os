"""file_extractions — cached readings of shared files (never the files themselves)

Revision ID: 0120
Revises: 0119
Create Date: 2026-08-25

Backs the agent-agnostic attachment intake layer. Stores what a file SAID, not
the file: bytes are fetched, extracted and dropped, so this table grows with
text people actually used rather than with every binary shared in Slack.

Nullable by design: `extracted_text` is NULL on rows that record a FAILURE, so
an unreadable file is not re-fetched every turn and "why was this not read" stays
answerable. Any query joining on extracted_text must handle NULL -- per CLAUDE.md,
a column added as nullable is NULL forever on rows that predate the reason it
exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "file_extractions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_id", sa.String(255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("mimetype", sa.String(255), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("tables", JSONB(), nullable=True),
        sa.Column("notes", JSONB(), nullable=True),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failure_kind", sa.String(64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("channel_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("shared_by", sa.String(32), nullable=False, server_default=""),
        sa.Column("message_ts", sa.String(32), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_read_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("source", "source_id", name="uq_file_extractions_source"),
    )
    op.create_index("ix_file_extractions_expiry", "file_extractions", ["promoted_at", "created_at"])
    op.create_index("ix_file_extractions_channel", "file_extractions", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_file_extractions_channel", table_name="file_extractions")
    op.drop_index("ix_file_extractions_expiry", table_name="file_extractions")
    op.drop_table("file_extractions")
