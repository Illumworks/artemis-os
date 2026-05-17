"""Pydantic DTOs for the Floating Artemis API surface.

Naming convention:
  *Create  — inbound request body (POST)
  *Update  — inbound request body (PATCH), all optional
  *Read    — outbound response (any method that returns a resource)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── Sessions ─────────────────────────────────────────────────────────────────


class SessionCreate(BaseModel):
    session_id: str = Field(..., min_length=1)
    owner_user_id: int | None = None
    title: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionUpdate(BaseModel):
    title: str | None = None
    metadata: dict[str, Any] | None = None


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    owner_user_id: int | None
    started_at: datetime
    last_active_at: datetime
    closed_at: datetime | None
    title: str | None
    metadata: dict[str, Any] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None

    @classmethod
    def from_orm_row(cls, row: Any) -> SessionRead:
        _p = getattr(row, "provider", None)
        _m = getattr(row, "model", None)
        return cls(
            id=row.id,
            session_id=row.session_id,
            owner_user_id=row.owner_user_id,
            started_at=row.started_at,
            last_active_at=row.last_active_at,
            closed_at=row.closed_at,
            title=row.title,
            metadata=row.metadata_ or {},
            provider=_p if isinstance(_p, str) else None,
            model=_m if isinstance(_m, str) else None,
        )


class SessionModelUpdate(BaseModel):
    """Request body for PATCH /sessions/{id}/model."""

    provider: str | None = None
    model: str | None = None


# ── Messages ─────────────────────────────────────────────────────────────────


class MessageCreate(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: list[dict[str, Any]]
    cost_input_tokens: int = 0
    cost_output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    role: str
    content: list[dict[str, Any]]
    cost_input_tokens: int
    cost_output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    created_at: datetime


# ── Page context ─────────────────────────────────────────────────────────────


class PageContextSet(BaseModel):
    page: str = Field(..., min_length=1)
    ref_id: str | None = None


class PageContextRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    page: str
    ref_id: str | None
    set_at: datetime


# ── Tool confirm ─────────────────────────────────────────────────────────────


class ToolConfirmRequest(BaseModel):
    tool_use_id: str
    decision: Literal["run", "cancel"]


class ToolConfirmResponse(BaseModel):
    tool_use_id: str
    decision: str
    result: str | None = None
    error: bool = False


# ── Turn (user message → start a chat turn) ───────────────────────────────────


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


# ── Voice corpus (internal, not fully exposed via routes) ─────────────────────


class VoiceCorpusRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line: str
    context_tag: str | None
    source: str
    use_count: int
    active: bool
    last_used_at: datetime


# ── Active runs (from view) ───────────────────────────────────────────────────


class ActiveRunRead(BaseModel):
    run_id: str
    run_type: str
    subject_id: str | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    owner_user_id: int | None
