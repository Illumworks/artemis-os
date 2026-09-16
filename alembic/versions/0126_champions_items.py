"""champions_items + champions_runs — the Champions digest store

Revision ID: 0126
Revises: 0125
Create Date: 2026-09-16

Phase 1 of the Champions community digest stores one row per community item.
Hannah's current version keeps this in a cache file on Drive; rows in Postgres
instead, for three reasons that a file cannot do: the domain -> pod join wants a
real join, the Google Sheet is rebuilt from scratch every run so the store has to
outlive it, and running the new pipeline alongside hers for a week is only an
acceptance test if the two can be diffed.

Two design notes worth keeping.

**The body is stored.** Classification is re-runnable and will be re-run: the
flagging rules changed once already (a keyword list that flagged 45% of posts
against a true rate of 1-3%), and re-pulling the corpus to re-label it is worse
than keeping 174k characters of text.

**The pod columns are nullable and separate from ingestion.** A domain that
cannot be placed is a row to review, not a row to drop, and resolution improves
over time as the pod directory is corrected in /pods/admin.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0126"
down_revision = "0125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "champions_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("item_type", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("posted_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("author_name", sa.Text(), nullable=True),
        sa.Column("author_email_domain", sa.Text(), nullable=True),
        sa.Column("is_amira_staff", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("district", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("pod", sa.Text(), nullable=True),
        sa.Column("csm_email", sa.Text(), nullable=True),
        sa.Column("pod_resolved_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("theme", sa.Text(), nullable=True),
        sa.Column("product_issue", sa.Boolean(), nullable=True),
        sa.Column("adoption_friction", sa.Boolean(), nullable=True),
        sa.Column("escalation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("escalation_terms", postgresql.JSONB(), nullable=True),
        sa.Column("classified_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("classifier_model", sa.Text(), nullable=True),
        sa.Column("replied_by_amira", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_id", name="uq_champions_items_external_id"),
    )
    op.create_index("idx_champions_items_posted_at", "champions_items", ["posted_at"])
    op.create_index("idx_champions_items_domain", "champions_items", ["author_email_domain"])
    op.create_index(
        "idx_champions_items_unclassified",
        "champions_items",
        ["posted_at"],
        postgresql_where=sa.text("classified_at IS NULL"),
    )

    op.create_table(
        "champions_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("watermark", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("items_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_classified", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_flagged", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("escalations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("champions_runs")
    op.drop_index("idx_champions_items_unclassified", table_name="champions_items")
    op.drop_index("idx_champions_items_domain", table_name="champions_items")
    op.drop_index("idx_champions_items_posted_at", table_name="champions_items")
    op.drop_table("champions_items")
