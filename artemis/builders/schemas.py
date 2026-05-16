"""Pydantic 2.x DTOs for the Builders domain.

Naming convention: <Model>Create (inbound), <Model>Read (outbound), <Model>Update (PATCH).
Field names are snake_case internally; aliases expose camelCase to match the Node API wire shape.

populate_by_name=True means tests can pass either style.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────────────────────


class AgentCreate(_Base):
    agent_id: str = Field(..., alias="agentId")
    name: str
    description: str | None = None
    goal: str | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    tools: list[Any] = Field(default_factory=list)
    model: str = "claude-sonnet-4-6"
    provider: str = "anthropic"
    max_iterations: int = Field(default=10, alias="maxIterations")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class AgentRead(_Base):
    id: int
    agent_id: str = Field(alias="agentId")
    name: str
    description: str | None = None
    goal: str | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    tools: list[Any] = Field(default_factory=list)
    model: str
    provider: str
    max_iterations: int = Field(alias="maxIterations")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AgentUpdate(_Base):
    name: str | None = None
    description: str | None = None
    goal: str | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    tools: list[Any] | None = None
    model: str | None = None
    provider: str | None = None
    max_iterations: int | None = Field(default=None, alias="maxIterations")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Runs
# ─────────────────────────────────────────────────────────────────────────────


class AgentRunRead(_Base):
    id: int
    run_id: str = Field(alias="runId")
    agent_id: str | None = Field(default=None, alias="agentId")
    status: str
    user_message: str | None = Field(default=None, alias="userMessage")
    shared_context: dict[str, Any] | None = Field(default=None, alias="sharedContext")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    cost_input_tokens: int = Field(alias="costInputTokens")
    cost_output_tokens: int = Field(alias="costOutputTokens")
    error: str | None = None
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Context
# ─────────────────────────────────────────────────────────────────────────────


class AgentContextRead(_Base):
    id: int
    run_id: str = Field(alias="runId")
    key: str
    value: Any
    created_at: datetime = Field(alias="createdAt")


# ─────────────────────────────────────────────────────────────────────────────
# Skills
# ─────────────────────────────────────────────────────────────────────────────


class SkillCreate(_Base):
    slug: str
    name: str
    description: str | None = None
    instructions: str | None = None
    tools: list[Any] = Field(default_factory=list)
    kind: str = "user"
    source_path: str | None = Field(default=None, alias="sourcePath")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class SkillRead(_Base):
    id: int
    slug: str
    name: str
    description: str | None = None
    instructions: str | None = None
    tools: list[Any] = Field(default_factory=list)
    kind: str
    source_path: str | None = Field(default=None, alias="sourcePath")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class SkillUpdate(_Base):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    tools: list[Any] | None = None
    kind: str | None = None
    source_path: str | None = Field(default=None, alias="sourcePath")


# ─────────────────────────────────────────────────────────────────────────────
# Workflows
# ─────────────────────────────────────────────────────────────────────────────


class WorkflowCreate(_Base):
    workflow_id: str = Field(..., alias="workflowId")
    name: str
    description: str | None = None
    steps: list[Any] = Field(default_factory=list)
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class WorkflowRead(_Base):
    id: int
    workflow_id: str = Field(alias="workflowId")
    name: str
    description: str | None = None
    steps: list[Any] = Field(default_factory=list)
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class WorkflowUpdate(_Base):
    name: str | None = None
    description: str | None = None
    steps: list[Any] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Runs
# ─────────────────────────────────────────────────────────────────────────────


class WorkflowRunRead(_Base):
    id: int
    run_id: str = Field(alias="runId")
    workflow_id: str | None = Field(default=None, alias="workflowId")
    status: str
    current_step: int = Field(alias="currentStep")
    claude_session_id: str | None = Field(default=None, alias="claudeSessionId")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    total_cost_usd: float = Field(alias="totalCostUsd")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Chains
# ─────────────────────────────────────────────────────────────────────────────


class AgentChainCreate(_Base):
    chain_id: str = Field(..., alias="chainId")
    name: str | None = None
    description: str | None = None
    steps: list[Any] = Field(default_factory=list)
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class AgentChainRead(_Base):
    id: int
    chain_id: str = Field(alias="chainId")
    name: str | None = None
    description: str | None = None
    steps: list[Any] = Field(default_factory=list)
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AgentChainUpdate(_Base):
    name: str | None = None
    description: str | None = None
    steps: list[Any] | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Agent DAGs
# ─────────────────────────────────────────────────────────────────────────────


class AgentDagCreate(_Base):
    dag_id: str = Field(..., alias="dagId")
    name: str | None = None
    description: str | None = None
    nodes: list[Any] = Field(default_factory=list)
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class AgentDagRead(_Base):
    id: int
    dag_id: str = Field(alias="dagId")
    name: str | None = None
    description: str | None = None
    nodes: list[Any] = Field(default_factory=list)
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")


class AgentDagUpdate(_Base):
    name: str | None = None
    description: str | None = None
    nodes: list[Any] | None = None
