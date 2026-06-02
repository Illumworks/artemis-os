"""SEND2-B — extend campaign_deliverables status check to include send states.

Adds 'queued_for_send' and 'sent' to the ck_campaign_deliverables_status
check constraint, which was tightened in 0034.

Revision ID: 0062
Revises: 0061
Create Date: 2026-06-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0062"
down_revision: str = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# New expanded set (original 7 + 2 send-pipeline states).
_DELIVERABLE_STATES_V2 = (
    "queued",
    "generating",
    "draft_ready",
    "approved",
    "queued_for_send",
    "sent",
    "revised",
    "rejected",
    "generation_failed",
)

# Original set from migration 0034 (used for downgrade).
_DELIVERABLE_STATES_V1 = (
    "queued",
    "generating",
    "draft_ready",
    "approved",
    "revised",
    "rejected",
    "generation_failed",
)


def _check_expr(states: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{s}'" for s in states)
    return f"status IN ({quoted})"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE campaign_deliverables "
        "DROP CONSTRAINT IF EXISTS ck_campaign_deliverables_status"
    )
    op.execute(
        "ALTER TABLE campaign_deliverables "
        f"ADD CONSTRAINT ck_campaign_deliverables_status "
        f"CHECK ({_check_expr(_DELIVERABLE_STATES_V2)})"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE campaign_deliverables "
        "DROP CONSTRAINT IF EXISTS ck_campaign_deliverables_status"
    )
    op.execute(
        "ALTER TABLE campaign_deliverables "
        f"ADD CONSTRAINT ck_campaign_deliverables_status "
        f"CHECK ({_check_expr(_DELIVERABLE_STATES_V1)})"
    )
