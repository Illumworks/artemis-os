"""Pydantic 2.x DTOs for the Builders domain.

Naming convention: <Model>Create (inbound), <Model>Read (outbound), <Model>Update (PATCH).
Field names are snake_case internally; aliases expose camelCase to match the Node API wire shape.

populate_by_name=True means tests can pass either style.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Allowed values for agent package policy fields (enforced at Pydantic boundary).
MemoryPolicy = Literal["session_scoped", "agent_scoped", "user_scoped", "none"]
PermissionMode = Literal["ask", "auto_approve", "dry_run"]
SkillStatus = Literal["proposed", "approved", "archived"]


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
    # J11 package policy fields
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    memory_policy: MemoryPolicy = Field(default="session_scoped", alias="memoryPolicy")
    permission_mode: PermissionMode = Field(default="ask", alias="permissionMode")
    output_contract: dict[str, Any] | None = Field(default=None, alias="outputContract")
    reason_codes_emitted: list[str] = Field(default_factory=list, alias="reasonCodesEmitted")
    metadata: dict[str, Any] = Field(default_factory=dict)


class PersonaData(_Base):
    """Persona/soul for an agent — all fields optional to allow partial updates."""

    name: str | None = None
    purpose: str | None = None
    voice_notes: str | None = Field(default=None, alias="voiceNotes")
    ghostwrite: bool = False
    profile_image_path: str | None = Field(default=None, alias="profileImagePath")


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
    # J11 package policy fields
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    memory_policy: str = Field(default="session_scoped", alias="memoryPolicy")
    permission_mode: str = Field(default="ask", alias="permissionMode")
    output_contract: dict[str, Any] | None = Field(default=None, alias="outputContract")
    reason_codes_emitted: list[str] = Field(default_factory=list, alias="reasonCodesEmitted")
    metadata: dict[str, Any] = Field(default_factory=dict, validation_alias="metadata_")
    # O2/O3 — persona/soul
    persona: dict[str, Any] | None = None
    cadence_seconds: int | None = Field(default=None, alias="cadenceSeconds")
    lifecycle_status: str | None = Field(default=None, alias="lifecycleStatus")
    urgency_tiers: dict[str, Any] | None = Field(default=None, alias="urgencyTiers")
    failure_modes: list[dict[str, Any]] | None = Field(default=None, alias="failureModes")
    db_tables_touched: list[str] | None = Field(default=None, alias="dbTablesTouched")
    implementation_notes: str | None = Field(default=None, alias="implementationNotes")
    inputs_required: list[dict[str, Any]] | None = Field(default=None, alias="inputsRequired")
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
    # J11 package policy fields
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    memory_policy: MemoryPolicy | None = Field(default=None, alias="memoryPolicy")
    permission_mode: PermissionMode | None = Field(default=None, alias="permissionMode")
    output_contract: dict[str, Any] | None = Field(default=None, alias="outputContract")
    reason_codes_emitted: list[str] | None = Field(default=None, alias="reasonCodesEmitted")
    metadata: dict[str, Any] | None = None
    # O2/O3 — persona/soul
    persona: dict[str, Any] | None = None


class PersonaPatch(_Base):
    """Body for PATCH /api/agents/{agent_id}/persona.

    All fields optional — caller sends only what changed.
    At least one field must be set (validated in the route handler).
    """

    name: str | None = None
    purpose: str | None = None
    voice_notes: str | None = Field(default=None, alias="voiceNotes")
    ghostwrite: bool | None = None
    profile_image_path: str | None = Field(default=None, alias="profileImagePath")


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
    is_ephemeral: bool = Field(default=False, alias="isEphemeral")


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
    category: str | None = None
    status: SkillStatus = "approved"
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
    category: str | None = None
    status: str
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
    category: str | None = None
    status: SkillStatus | None = None
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
