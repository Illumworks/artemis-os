"""agent_traces table for P6 self-evolution trace capture.

Revision ID: 0096
Revises: 0095
Create Date: 2026-06-16

Adds the ``agent_traces`` table — the structured per-turn execution trace
store that seeds the P6 self-evolution loop.

Design notes:

- One row per agent turn (floating-agent *or* executor run).  Written
  fire-and-forget at turn end; never blocks the user-facing response.
- Complements (does NOT duplicate) ``agent_run_trajectory_summaries``:
    * Trajectory summaries = LLM-generated, per agent_run, narrative text.
    * agent_traces = structured, zero-LLM, per-turn runtime fields
      (latency, tokens, tool list, error, outcome).
- ``agent_id + created_at`` compound index satisfies the primary P6 query
  pattern: "recent traces for agent X in time window Y".
- No FK to ``agent_runs`` — floating-agent turns have no agent_run row.
  The session_id + agent_id pair is enough for join-free analysis.
- ``owner_user_id`` mirrors the pattern on ``agent_runs``; NULL for
  system/cron turns.
- ``outcome`` is a free TEXT column (no CHECK constraint) so future callers
  can extend values without a migration. The capture hook writes one of
  "success" | "error" | "partial" | "tool_pending".

Do NOT apply until Lead merges worker/trace-capture and runs
``uv run alembic upgrade head``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0096"
down_revision: str | None = "0095"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_traces",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        # identity
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=True),
        # routing / model
        sa.Column("feature_tag", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        # content digest
        sa.Column("input_summary", sa.Text(), nullable=True),
        sa.Column(
            "tools_used",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("output_summary", sa.Text(), nullable=True),
        # outcome
        sa.Column(
            "outcome",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'success'"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        # cost / latency
        sa.Column("latency_ms", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        # ownership
        sa.Column("owner_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_agent_traces_agent_created",
        "agent_traces",
        ["agent_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_agent_traces_session_id",
        "agent_traces",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_agent_traces_session_id", table_name="agent_traces")
    op.drop_index("idx_agent_traces_agent_created", table_name="agent_traces")
    op.drop_table("agent_traces")
