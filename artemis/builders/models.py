"""SQLAlchemy 2.x async ORM models for the Builders domain.

Tables (all Postgres, all TIMESTAMPTZ timestamps, all JSONB blobs):
  agents         — agent definitions (slug, prompt, tools, model)
  agent_runs     — execution run records (queued → running → completed/failed)
  agent_context  — per-run key/value store for inter-step data passing
  skills         — user/builtin/plugin skill definitions
  workflows      — step-based sequential workflow definitions
  workflow_runs  — workflow execution run records
  agent_chains   — sequential agent pipeline definitions
  agent_dags     — dependency-graph agent pipeline definitions

Phase F2a: data layer only. Execution wiring (F2b) wires these to the F1 loop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from artemis.db import Base


class Agent(Base):
    """Agent definition — slug-addressed, holds prompt + tool list + model config."""

    __tablename__ = "agents"
    __table_args__ = (Index("idx_agents_agent_id", "agent_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    model: Mapped[str] = mapped_column(Text, nullable=False, server_default="claude-sonnet-4-6")
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="anthropic")
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRun(Base):
    """Agent execution run — lifecycle: queued → running → completed|failed|cancelled."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("idx_agent_runs_agent_status", "agent_id", "status"),
        Index("idx_agent_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    agent_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("agents.agent_id", name="fk_agent_runs_agent", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    shared_context: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    cost_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cost_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_ephemeral: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


class AgentContext(Base):
    """Per-run key/value store for sharing output between agent steps.

    Either ``run_id`` (agent run) or ``workflow_run_id`` (workflow run) must be
    set, but not both. The CHECK constraint enforcing this is added by migration
    0008_workflow_context.
    """

    __tablename__ = "agent_context"
    __table_args__ = (
        # The unique constraint covers BOTH key columns so agent and workflow
        # rows can coexist without conflicts.
        UniqueConstraint("run_id", "key", name="uq_agent_context_run_key"),
        Index("idx_agent_context_run_id", "run_id"),
        Index("idx_agent_context_workflow_run_id", "workflow_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("agent_runs.run_id", name="fk_agent_context_run", ondelete="CASCADE"),
        nullable=True,
    )
    workflow_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("workflow_runs.id", name="fk_agent_context_workflow_run", ondelete="CASCADE"),
        nullable=True,
    )
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class Skill(Base):
    """Skill definition — user/builtin/plugin, holds instructions + tools."""

    __tablename__ = "skills"
    __table_args__ = (Index("idx_skills_slug", "slug"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    tools: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="user")
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class Workflow(Base):
    """Sequential workflow — an ordered list of prompted steps sharing one Claude session."""

    __tablename__ = "workflows"
    __table_args__ = (Index("idx_workflows_workflow_id", "workflow_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workflow_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class WorkflowRun(Base):
    """Workflow execution run — tracks step progress and accumulated cost."""

    __tablename__ = "workflow_runs"
    __table_args__ = (Index("idx_workflow_runs_workflow_id", "workflow_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    workflow_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("workflows.workflow_id", name="fk_workflow_runs_workflow", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claude_session_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    total_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class AgentChain(Base):
    """Sequential agent pipeline — list of agents that share a run_id for cost tracking."""

    __tablename__ = "agent_chains"
    __table_args__ = (Index("idx_agent_chains_chain_id", "chain_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chain_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    steps: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AgentDag(Base):
    """Dependency-graph agent pipeline — nodes + depends_on for parallel execution."""

    __tablename__ = "agent_dags"
    __table_args__ = (Index("idx_agent_dags_dag_id", "dag_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dag_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    nodes: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
