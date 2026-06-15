"""Add routing_status column to signal_queue.

routing_status is a SEPARATE dimension from signal_status (lifecycle).
It records whether a signal has a routable contact at write time.

Allowed values:
  routable              — resolved_district_id is set AND an active
                          district_contacts row exists.
  unrouted_no_contact   — no routable contact resolved (state-level signals,
                          unknown districts, districts with no active contact).

Default is 'routable' so existing rows are not incorrectly labelled
unroutable (they predate this column and were written before contact
classification was available).

Revision ID: 0092
Revises: 0091
Create Date: 2026-06-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0092"
down_revision: str | None = "0091"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "signal_queue",
        sa.Column(
            "routing_status",
            sa.Text(),
            nullable=False,
            server_default="routable",
        ),
    )
    op.create_index(
        "idx_signal_queue_routing_status",
        "signal_queue",
        ["routing_status"],
    )


def downgrade() -> None:
    op.drop_index("idx_signal_queue_routing_status", table_name="signal_queue")
    op.drop_column("signal_queue", "routing_status")
