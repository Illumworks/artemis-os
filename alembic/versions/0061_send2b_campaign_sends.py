"""SEND2-B — campaign_sends table for outbox + send state machine.

Revision ID: 0061
Revises: 0060
Create Date: 2026-06-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0061"
down_revision: str = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "campaign_sends",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), nullable=False),
        sa.Column("deliverable_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "recipients",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column(
            "transport",
            sa.Text(),
            nullable=False,
            server_default="stub",
        ),
        sa.Column(
            "transport_log",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "queued_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("sent_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued','sent','failed','skipped')",
            name="ck_campaign_sends_status",
        ),
        sa.CheckConstraint(
            "transport IN ('stub')",
            name="ck_campaign_sends_transport",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["campaign_candidates.id"],
            name="fk_campaign_sends_candidate",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deliverable_id"],
            ["campaign_deliverables.id"],
            name="fk_campaign_sends_deliverable",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "idx_campaign_sends_status_queued_at",
        "campaign_sends",
        ["status", "queued_at"],
    )
    op.create_index(
        "idx_campaign_sends_candidate",
        "campaign_sends",
        ["candidate_id"],
    )
    op.create_index(
        "idx_campaign_sends_deliverable",
        "campaign_sends",
        ["deliverable_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_campaign_sends_deliverable", table_name="campaign_sends")
    op.drop_index("idx_campaign_sends_candidate", table_name="campaign_sends")
    op.drop_index("idx_campaign_sends_status_queued_at", table_name="campaign_sends")
    op.drop_constraint("fk_campaign_sends_deliverable", "campaign_sends", type_="foreignkey")
    op.drop_constraint("fk_campaign_sends_candidate", "campaign_sends", type_="foreignkey")
    op.drop_table("campaign_sends")
