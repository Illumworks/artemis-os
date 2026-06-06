"""Add feature_routing_overrides, routing_changes_log, and app_settings tables.

Part of the routing control surface (briefs/routing-control-surface.md).

Tables created:
  feature_routing_overrides  — per-feature cascade overrides (one row per tag)
  routing_changes_log        — lossless audit log (never deleted)
  app_settings               — key/value store for runtime config (default_cascade)

Revision ID: 0066
Revises: 0065
Create Date: 2026-06-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0066"
down_revision: str = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── feature_routing_overrides ──────────────────────────────────────────
    op.create_table(
        "feature_routing_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("feature_tag", sa.Text(), nullable=False),
        sa.Column("cascade", JSONB(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Text(), server_default="operator", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feature_tag"),
    )
    op.create_index("ix_feature_routing_overrides_feature_tag", "feature_routing_overrides", ["feature_tag"])

    # ── routing_changes_log ────────────────────────────────────────────────
    op.create_table(
        "routing_changes_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.Text(), server_default="operator", nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("scope_value", sa.Text(), nullable=True),
        sa.Column("before", JSONB(), nullable=True),
        sa.Column("after", JSONB(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_routing_changes_log_changed_at", "routing_changes_log", ["changed_at"])

    # ── app_settings ───────────────────────────────────────────────────────
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_index("ix_routing_changes_log_changed_at", table_name="routing_changes_log")
    op.drop_table("routing_changes_log")
    op.drop_index("ix_feature_routing_overrides_feature_tag", table_name="feature_routing_overrides")
    op.drop_table("feature_routing_overrides")
