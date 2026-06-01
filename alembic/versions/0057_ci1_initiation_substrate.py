"""CI1 — campaign initiation substrate + deterministic grouping foundation.

Revision ID: 0057
Revises: 0056
Create Date: 2026-06-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0057"
down_revision: str = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("campaign_candidates", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("campaign_candidates", sa.Column("objective", sa.Text(), nullable=True))
    op.add_column(
        "campaign_candidates",
        sa.Column("target_scope_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "campaign_candidates",
        sa.Column(
            "deliverable_types_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "campaign_candidates",
        sa.Column("initiated_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("campaign_candidates", sa.Column("initiated_by", sa.BigInteger(), nullable=True))
    op.add_column(
        "campaign_candidates",
        sa.Column("predecessor_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_campaign_candidates_predecessor",
        "campaign_candidates",
        "campaign_candidates",
        ["predecessor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_campaign_candidates_predecessor",
        "campaign_candidates",
        ["predecessor_id"],
    )

    op.create_table(
        "deliverable_types",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column(
            "default_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_deliverable_types"),
        sa.UniqueConstraint("slug", name="uq_deliverable_types_slug"),
    )
    op.create_index(
        "idx_deliverable_types_active_order",
        "deliverable_types",
        ["active", "display_order"],
    )

    deliverable_types = sa.table(
        "deliverable_types",
        sa.column("slug", sa.Text()),
        sa.column("label", sa.Text()),
        sa.column("default_enabled", sa.Boolean()),
        sa.column("active", sa.Boolean()),
        sa.column("display_order", sa.Integer()),
    )
    op.bulk_insert(
        deliverable_types,
        [
            {
                "slug": "outreach_email",
                "label": "Outreach Email",
                "default_enabled": True,
                "active": True,
                "display_order": 1,
            },
            {
                "slug": "social",
                "label": "Social Post",
                "default_enabled": False,
                "active": False,
                "display_order": 2,
            },
            {
                "slug": "long_form",
                "label": "Long-Form",
                "default_enabled": False,
                "active": False,
                "display_order": 3,
            },
            {
                "slug": "landing_page",
                "label": "Landing Page",
                "default_enabled": False,
                "active": False,
                "display_order": 4,
            },
        ],
    )

    op.create_table(
        "campaign_candidate_signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("signal_id", sa.BigInteger(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "attached_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["campaign_candidates.id"],
            name="fk_campaign_candidate_signals_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["signal_id"],
            ["signal_queue.id"],
            name="fk_campaign_candidate_signals_signal",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_campaign_candidate_signals"),
        sa.UniqueConstraint(
            "candidate_id",
            "signal_id",
            name="uq_campaign_candidate_signals_candidate_signal",
        ),
    )
    op.create_index(
        "idx_campaign_candidate_signals_candidate",
        "campaign_candidate_signals",
        ["candidate_id"],
    )
    op.create_index(
        "idx_campaign_candidate_signals_signal",
        "campaign_candidate_signals",
        ["signal_id"],
    )

    op.create_table(
        "marketing_clustering_config",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "cluster_window_days",
            sa.Integer(),
            nullable=False,
            server_default="90",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_clustering_config"),
    )
    clustering_config = sa.table(
        "marketing_clustering_config",
        sa.column("cluster_window_days", sa.Integer()),
    )
    op.bulk_insert(clustering_config, [{"cluster_window_days": 90}])


def downgrade() -> None:
    op.drop_table("marketing_clustering_config")
    op.drop_index(
        "idx_campaign_candidate_signals_signal",
        table_name="campaign_candidate_signals",
    )
    op.drop_index(
        "idx_campaign_candidate_signals_candidate",
        table_name="campaign_candidate_signals",
    )
    op.drop_table("campaign_candidate_signals")
    op.drop_index("idx_deliverable_types_active_order", table_name="deliverable_types")
    op.drop_table("deliverable_types")
    op.drop_index("idx_campaign_candidates_predecessor", table_name="campaign_candidates")
    op.drop_constraint(
        "fk_campaign_candidates_predecessor",
        "campaign_candidates",
        type_="foreignkey",
    )
    op.drop_column("campaign_candidates", "predecessor_id")
    op.drop_column("campaign_candidates", "initiated_by")
    op.drop_column("campaign_candidates", "initiated_at")
    op.drop_column("campaign_candidates", "deliverable_types_json")
    op.drop_column("campaign_candidates", "target_scope_json")
    op.drop_column("campaign_candidates", "objective")
    op.drop_column("campaign_candidates", "name")
