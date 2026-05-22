"""agents_reason_codes_emitted — runtime reason-code emission allowlist.

Revision ID: 0039
Revises: 0038
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "reason_codes_emitted",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "reason_codes_emitted")
