"""CC21 — tool_invocations scope-aware: add builder_session_id + relax agent_run_id.

Revision ID: 0050
Revises: 0048
Create Date: 2026-05-30

Changes:
  - tool_invocations.agent_run_id relaxed to nullable=True.
  - tool_invocations.builder_session_id (BigInteger, nullable) added.
  - CHECK constraint ck_tool_invocations_scope: exactly one of
    (agent_run_id, builder_session_id) must be NOT NULL.
  - Partial index idx_tool_invocations_builder_session on builder_session_id
    WHERE builder_session_id IS NOT NULL — fast Builder-session lookups.

  Existing rows have agent_run_id populated; the CHECK passes for them
  unchanged.  New Builder-tool rows land with builder_session_id populated
  and agent_run_id NULL.

  Bundle B + Bundle A coexist: A claims 0049, B claims 0050+0051.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column("builder_session_id", sa.BigInteger(), nullable=True),
    )
    op.alter_column("tool_invocations", "agent_run_id", nullable=True)
    op.create_check_constraint(
        "ck_tool_invocations_scope",
        "tool_invocations",
        "(agent_run_id IS NOT NULL AND builder_session_id IS NULL) OR "
        "(agent_run_id IS NULL AND builder_session_id IS NOT NULL)",
    )
    op.create_index(
        "idx_tool_invocations_builder_session",
        "tool_invocations",
        ["builder_session_id"],
        postgresql_where=sa.text("builder_session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_tool_invocations_builder_session", table_name="tool_invocations"
    )
    op.drop_constraint("ck_tool_invocations_scope", "tool_invocations", type_="check")
    op.alter_column("tool_invocations", "agent_run_id", nullable=False)
    op.drop_column("tool_invocations", "builder_session_id")
