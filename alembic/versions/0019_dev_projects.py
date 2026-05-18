"""dev_projects — project-scoped coding sessions.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dev_projects",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("path", sa.Text, nullable=False, unique=True),
        sa.Column(
            "last_opened_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "metadata", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
    )

    op.create_table(
        "dev_sessions",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.BigInteger,
            sa.ForeignKey("dev_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text, nullable=True),
        sa.Column("provider", sa.Text, nullable=False, server_default="claude-code"),
        sa.Column("model", sa.Text, nullable=True),
        sa.Column(
            "bypass_permissions", sa.Boolean, nullable=False, server_default=sa.text("false")
        ),
        sa.Column("notes", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_active_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("archived_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "fork_of",
            sa.BigInteger,
            sa.ForeignKey("dev_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fork_at_message", sa.BigInteger, nullable=True),
    )
    op.create_index("ix_dev_sessions_project", "dev_sessions", ["project_id", "last_active_at"])

    op.create_table(
        "dev_messages",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.BigInteger,
            sa.ForeignKey("dev_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", postgresql.JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_dev_messages_session", "dev_messages", ["session_id", "created_at"])

    op.create_table(
        "dev_annotations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.BigInteger,
            sa.ForeignKey("dev_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text, nullable=True),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_dev_annotations_session", "dev_annotations", ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_dev_annotations_session", table_name="dev_annotations")
    op.drop_table("dev_annotations")
    op.drop_index("ix_dev_messages_session", table_name="dev_messages")
    op.drop_table("dev_messages")
    op.drop_index("ix_dev_sessions_project", table_name="dev_sessions")
    op.drop_table("dev_sessions")
    op.drop_table("dev_projects")
