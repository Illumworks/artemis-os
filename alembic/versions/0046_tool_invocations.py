"""CC17 — tool_invocations table for MCP-path tool call logging.

Revision ID: 0046
Revises: 0045
Create Date: 2026-05-28

Changes:
  - tool_invocations table created.
    Every MCP tool call is logged here by the mcp_server subprocess, committed
    independently per invocation.  Snapshot extraction in executor.py reads
    from this table so the trajectory summarizer sees real tool calls for
    claude-code-provider agents (where result.messages is empty).

    No FK on agent_run_id — tool invocations may land during the same long
    pipeline transaction that creates the agent_run row (same CC14 race shape).
    Logical reference only; indexed for fast per-run queries.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_invocations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("agent_run_id", sa.Text, nullable=False),
        sa.Column("pipeline_run_id", sa.Text, nullable=True),
        sa.Column("tool_name", sa.Text, nullable=False),
        sa.Column("args_summary", sa.Text, nullable=True),
        sa.Column("result_preview", sa.Text, nullable=True),
        sa.Column("success", sa.Boolean, nullable=False),
        sa.Column(
            "invoked_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tool_invocations_agent_run_id", "tool_invocations", ["agent_run_id"])
    op.create_index("ix_tool_invocations_pipeline_run_id", "tool_invocations", ["pipeline_run_id"])


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_pipeline_run_id", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_agent_run_id", table_name="tool_invocations")
    op.drop_table("tool_invocations")
