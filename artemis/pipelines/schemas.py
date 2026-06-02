"""Pydantic 2.x schemas for the Pipelines domain (PIPE1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ── JSONB shape declarations (TypedDicts for Lead verification) ───────────────


class PipelineNode(TypedDict):
    """Node JSONB shape for a pipeline. Stored inside pipelines.nodes list."""

    id: str  # uuid or human slug — must be unique within the pipeline
    type: Literal[
        "agent_invocation",
        "skill_call",
        "trigger_manual",
        "trigger_scheduled",
        "trigger_webhook",
        "trigger_event",
        "human_gate",
        "conditional",
        "sub_pipeline",
    ]
    label: str  # display name
    config: dict[str, Any]  # type-specific config (which agent, what cron, etc.)
    position: dict[str, float]  # {x, y} for canvas placement (used by PIPE2; ignored by PIPE1)


class PipelineEdge(TypedDict):
    """Edge JSONB shape for a pipeline. Stored inside pipelines.edges list."""

    id: str
    source_node_id: str
    target_node_id: str
    condition: dict[str, Any] | None  # optional gate condition for conditional branches
    data_shape: dict[str, Any] | None  # optional output→input mapping hint (for PIPE2 inspection)


# ── Pydantic request / response schemas ──────────────────────────────────────

_VALID_NODE_TYPES = frozenset(
    [
        "agent_invocation",
        "skill_call",
        "trigger_manual",
        "trigger_scheduled",
        "trigger_webhook",
        "trigger_event",
        "human_gate",
        "conditional",
        "sub_pipeline",
    ]
)

_VALID_STATUSES = frozenset(["active", "paused", "archived"])


class PipelineCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    trigger_config: dict[str, Any] | None = Field(default=None, alias="triggerConfig")
    status: str = "active"
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> PipelineCreate:
        _validate_nodes_and_edges(self.nodes, self.edges)
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        return self


class PipelineUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    trigger_config: dict[str, Any] | None = Field(default=None, alias="triggerConfig")
    status: str | None = None
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_graph(self) -> PipelineUpdate:
        # Only validate if both nodes + edges are supplied together
        nodes = self.nodes
        edges = self.edges
        if nodes is not None and edges is not None:
            _validate_nodes_and_edges(nodes, edges)
        elif edges is not None and nodes is None:
            # Edges without nodes — can't validate referential integrity here;
            # caller should supply nodes too. Allow pass-through.
            pass
        if self.status is not None and self.status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        return self


class PipelineRunRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    pipeline_id: str = Field(alias="pipelineId")
    status: str
    trigger: str
    triggered_by: str | None = Field(default=None, alias="triggeredBy")
    target_candidate_id: int | None = Field(default=None, alias="targetCandidateId")
    node_states: dict[str, Any] = Field(default_factory=dict, alias="nodeStates")
    started_at: datetime | None = Field(default=None, alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    error_message: str | None = Field(default=None, alias="errorMessage")
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")


class PipelineRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = None
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    trigger_config: dict[str, Any] | None = Field(default=None, alias="triggerConfig")
    status: str
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    latest_run: PipelineRunRead | None = Field(default=None, alias="latestRun")


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    triggered_by: str | None = Field(default=None, alias="triggeredBy")
    metadata: dict[str, Any] | None = None


class AgentExport(BaseModel):
    agent_id: str
    name: str
    description: str | None = None
    goal: str | None = None
    system_prompt: str | None = None
    tools: list[Any] = Field(default_factory=list)
    persona: dict[str, Any] | None = None
    model: str
    provider: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    memory_policy: str = "session_scoped"
    permission_mode: str = "ask"
    output_contract: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConnectorRequirement(BaseModel):
    kind: str
    label: str
    fields_needed: list[str] = Field(default_factory=list)


class PipelineExportBundle(BaseModel):
    format_version: str
    exported_at: datetime
    exported_from: str | None = None
    pipeline: dict[str, Any]
    agents_required: list[AgentExport] = Field(default_factory=list)
    connectors_required: list[ConnectorRequirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_format(self) -> PipelineExportBundle:
        if self.format_version != "1":
            raise ValueError("Format upgrade required: only format_version '1' is supported")
        return self


class PipelineImportResult(BaseModel):
    pipeline_id: str
    agents_created: list[str] = Field(default_factory=list)
    agents_skipped: list[str] = Field(default_factory=list)
    import_warnings: list[str] = Field(default_factory=list)


# ── Helper: build schema objects from ORM rows ────────────────────────────────


def pipeline_run_to_schema(run: Any) -> PipelineRunRead:
    """Build a PipelineRunRead from an ORM row or dict-like."""
    return PipelineRunRead(
        id=run.id,
        pipeline_id=run.pipeline_id,
        status=run.status,
        trigger=run.trigger,
        triggered_by=run.triggered_by,
        target_candidate_id=getattr(run, "target_candidate_id", None),
        node_states=run.node_states or {},
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        metadata=run.metadata_,
        created_at=run.created_at,
    )


def pipeline_to_schema(p: Any, latest_run: Any | None = None) -> PipelineRead:
    """Build a PipelineRead from an ORM row, optionally embedding the latest run."""
    return PipelineRead(
        id=p.id,
        name=p.name,
        description=p.description,
        nodes=p.nodes or [],
        edges=p.edges or [],
        trigger_config=p.trigger_config,
        status=p.status,
        owner_user_id=p.owner_user_id,
        metadata=p.metadata_,
        created_at=p.created_at,
        updated_at=p.updated_at,
        latest_run=pipeline_run_to_schema(latest_run) if latest_run else None,
    )


# ── Validation helpers ────────────────────────────────────────────────────────


def _validate_nodes_and_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    """Validate node-ID uniqueness and edge referential integrity."""
    node_ids: set[str] = set()
    for node in nodes:
        nid = node.get("id")
        if not nid:
            raise ValueError("Each node must have a non-empty 'id' field")
        ntype = node.get("type")
        if ntype not in _VALID_NODE_TYPES:
            raise ValueError(
                f"Invalid node type '{ntype}'. Must be one of {sorted(_VALID_NODE_TYPES)}"
            )
        if nid in node_ids:
            raise ValueError(
                f"Duplicate node id '{nid}' — node IDs must be unique within a pipeline"
            )
        node_ids.add(nid)

    for edge in edges:
        src = edge.get("source_node_id")
        tgt = edge.get("target_node_id")
        if src not in node_ids:
            raise ValueError(f"Edge source_node_id '{src}' does not reference an existing node")
        if tgt not in node_ids:
            raise ValueError(f"Edge target_node_id '{tgt}' does not reference an existing node")
