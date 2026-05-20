"""Pydantic schemas for the Builder API (O1).

Wire shapes for builder_sessions, definition_proposals,
and agent_run_trajectory_summaries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Builder sessions ──────────────────────────────────────────────────────────


class BuilderSessionCreate(BaseModel):
    """Body for POST /api/builder/sessions."""

    builder_kind: str = Field(default="agent", pattern="^(agent|skill|workflow|automation)$")
    target_id: int | None = None
    user_id: str | None = None


class BuilderSessionRead(BaseModel):
    """Wire shape returned from GET /api/builder/sessions/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    builder_kind: str
    target_id: int | None = None
    user_id: str | None = None
    status: str
    conversation: list[Any] = Field(default_factory=list)
    draft: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class BuilderMessageCreate(BaseModel):
    """Body for POST /api/builder/sessions/{id}/messages."""

    content: str


class BuilderMessageResponse(BaseModel):
    """Response from the builder after processing a user message."""

    session_id: int
    assistant_text: str
    draft: dict[str, Any] | None = None
    stop_reason: str


# ── Definition proposals ──────────────────────────────────────────────────────


class DefinitionProposalRead(BaseModel):
    """Wire shape for GET /api/builder/proposals/{id}."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    builder_session_id: int | None = None
    kind: str
    target_id: int | None = None
    proposed_by: str
    proposed_definition: dict[str, Any]
    citations: dict[str, Any] | None = None
    status: str
    created_at: datetime


# ── Trajectory summaries ──────────────────────────────────────────────────────


class TrajectorySummaryRead(BaseModel):
    """Wire shape for a trajectory summary row."""

    model_config = ConfigDict(from_attributes=True)

    run_id: int
    what_worked: str | None = None
    what_stalled: str | None = None
    what_was_missing: str | None = None
    generated_at: datetime


# ── Builder context (for GET /api/agents/{id}/builder-context) ─────────────────


class AgentBuilderContext(BaseModel):
    """What the builder sees when you open an existing agent."""

    agent_id: str
    recent_runs: list[dict[str, Any]] = Field(default_factory=list)
    trajectory_summaries: list[TrajectorySummaryRead] = Field(default_factory=list)
    pending_proposals: list[DefinitionProposalRead] = Field(default_factory=list)
