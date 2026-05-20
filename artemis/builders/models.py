"""SQLAlchemy 2.x async ORM models for the Builders domain.

Tables (all Postgres, all TIMESTAMPTZ timestamps, all JSONB blobs):
  agents         — agent definitions (slug, prompt, tools, model)
  agent_runs     — execution run records (queued → running → completed/failed)
  agent_context  — per-run key/value store for inter-step data passing
  agent_skills   — many-to-many join: agents ↔ skills (J11)
  skills         — user/builtin/plugin skill definitions
  workflows      — step-based sequential workflow definitions
  workflow_runs  — workflow execution run records
  agent_chains   — sequential agent pipeline definitions
  agent_dags     — dependency-graph agent pipeline definitions

O1 — Agent-Builder + Self-Improvement tables:
  builder_sessions                  — in-flight builder conversations
  definition_proposals              — proposed definitions awaiting approval
  agent_run_trajectory_summaries    — per-run self-improvement input summaries

Phase F2a: data layer only. Execution wiring (F2b) wires these to the F1 loop.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
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
    # J11 — package policy fields
    fallback_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="session_scoped"
    )
    permission_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="ask")
    output_contract: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AgentSkill(Base):
    """Many-to-many join: agents ↔ skills. Added in J11."""

    __tablename__ = "agent_skills"
    __table_args__ = (Index("ix_agent_skills_skill_slug", "skill_slug"),)

    agent_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("agents.id", name="fk_agent_skills_agent", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    skill_slug: Mapped[str] = mapped_column(
        Text,
        ForeignKey("skills.slug", name="fk_agent_skills_skill", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    assigned_at: Mapped[datetime] = mapped_column(
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
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'approved', 'archived')",
            name="ck_skills_status",
        ),
        Index("idx_skills_slug", "slug"),
        Index("idx_skills_status", "status"),
        Index("idx_skills_category", "category"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="approved")
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


# ── O1 — Agent-Builder + Self-Improvement models ─────────────────────────────


class BuilderSession(Base):
    """In-flight builder conversation.

    Tracks the full message history and current draft of a meta-object being
    built via the conversational Agent-Builder interface.

    builder_kind: 'agent' | 'skill' | 'workflow' | 'automation' (only 'agent' in v1)
    target_id: non-null = edit session for an existing definition; null = new
    status: 'active' | 'committed' | 'abandoned'
    conversation: full message history as [{role, content}] for resumption
    draft: current draft of the meta-object accumulating from the conversation
    """

    __tablename__ = "builder_sessions"
    __table_args__ = (
        CheckConstraint(
            "builder_kind IN ('agent', 'skill', 'workflow', 'automation')",
            name="ck_builder_sessions_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'committed', 'abandoned')",
            name="ck_builder_sessions_status",
        ),
        Index("idx_builder_sessions_status", "status"),
        Index("idx_builder_sessions_user_id", "user_id"),
        Index("idx_builder_sessions_kind", "builder_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    builder_kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    conversation: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="'[]'")
    draft: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class DefinitionProposal(Base):
    """Proposed definition awaiting user approval.

    Records a draft definition from the builder or self-improvement loop.
    The proposal state machine: pending → approved | rejected | superseded.

    proposed_by: 'user' | 'builder' | 'self-improvement'
    target_id: non-null = revision of existing; null = new
    citations: e.g. {"run_ids": [47, 51, 53], "rationale": "..."}
    """

    __tablename__ = "definition_proposals"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('agent', 'skill', 'workflow', 'automation')",
            name="ck_definition_proposals_kind",
        ),
        CheckConstraint(
            "proposed_by IN ('user', 'builder', 'self-improvement')",
            name="ck_definition_proposals_proposed_by",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'superseded')",
            name="ck_definition_proposals_status",
        ),
        Index("idx_definition_proposals_status", "status"),
        Index("idx_definition_proposals_session_id", "builder_session_id"),
        Index("idx_definition_proposals_kind_target", "kind", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    builder_session_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "builder_sessions.id",
            name="fk_definition_proposals_session",
            ondelete="SET NULL",
        ),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposed_by: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_definition: Mapped[Any] = mapped_column(JSONB, nullable=False)
    citations: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )


class AgentRunTrajectorySummary(Base):
    """Per-run trajectory summary — input to the self-improvement loop.

    Written asynchronously after every agent run completes.
    run_id is both the PK and the FK to agent_runs.id.
    """

    __tablename__ = "agent_run_trajectory_summaries"
    __table_args__ = (
        Index("idx_trajectory_summaries_generated_at", "generated_at"),
    )

    run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "agent_runs.id",
            name="fk_trajectory_summaries_run",
            ondelete="CASCADE",
        ),
        primary_key=True,
        nullable=False,
    )
    what_worked: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_stalled: Mapped[str | None] = mapped_column(Text, nullable=True)
    what_was_missing: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
