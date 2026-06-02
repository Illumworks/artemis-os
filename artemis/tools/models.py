"""ORM model for the tool_invocations table (CC17).

Every MCP tool call is written here by mcp_server._call_tool, committed
independently per invocation.  Snapshot extraction in executor._build_snapshot
reads from this table to populate tool_calls in AgentRunSnapshot — giving the
trajectory summarizer ground-truth visibility into claude-code-path tool calls
that never appear in result.messages.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, BigInteger, Boolean, CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class ToolInvocation(Base):
    """One row per MCP tool call.

    Scope: each row is owned by exactly one of two scopes —
      - agent_run_id (Text, UUID string matching agent_runs.run_id) for tools
        invoked by the pipeline/agent-run path (CC17 original behavior).
      - builder_session_id (BigInteger FK-less ref to builder_sessions.id)
        for tools invoked by the Agent-Builder path (CC19/CC21).
    The CHECK constraint ck_tool_invocations_scope enforces exactly one is set.
    No FK on either — tool invocations may commit before the owning row itself
    does (CC14 race shape).  Indexed for fast per-scope queries.

    tool_name: artemis-style name, e.g. "signal_queue.write" (not the MCP
        name "mcp__artemis__signal_queue_write").
    args_summary: truncated/JSON representation of call arguments, ≤500 chars.
    result_preview: first ≤500 chars of the tool's return string.
    success: False when the tool raised, or when the result starts with
        VALIDATION_ERROR / PERMISSION_DENIED / STUB: / TOOL_ERROR / UNKNOWN_TOOL.
    invoked_at: server-default timestamp; set by Postgres on INSERT.
    """

    __tablename__ = "tool_invocations"
    __table_args__ = (
        CheckConstraint(
            "(agent_run_id IS NOT NULL AND builder_session_id IS NULL) OR "
            "(agent_run_id IS NULL AND builder_session_id IS NOT NULL)",
            name="ck_tool_invocations_scope",
        ),
        Index("ix_tool_invocations_agent_run_id", "agent_run_id"),
        Index("ix_tool_invocations_pipeline_run_id", "pipeline_run_id"),
        Index(
            "idx_tool_invocations_builder_session",
            "builder_session_id",
            postgresql_where=text("builder_session_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    builder_session_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pipeline_run_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    args_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    invoked_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default="now()",
    )
