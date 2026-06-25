"""Pydantic schemas for Dev Projects."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DevProjectCreate(BaseModel):
    name: str = Field(min_length=1)
    path: str = Field(min_length=1)


class DevProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    archived: bool | None = None
    metadata: dict[str, Any] | None = None


class DevProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    path: str
    last_opened_at: datetime
    archived_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


_ForgeModeT = Literal["read", "write"] | None


class DevSessionCreate(BaseModel):
    provider: str | None = None
    model: str | None = None
    title: str | None = None
    forge_mode: _ForgeModeT = None


class DevSessionUpdate(BaseModel):
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    bypass_permissions: bool | None = None
    pinned: bool | None = None
    archived: bool | None = None
    forge_mode: _ForgeModeT = None


class DevSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    title: str | None
    provider: str
    model: str | None
    bypass_permissions: bool
    pinned: bool
    notes: list[dict[str, Any]]
    started_at: datetime
    last_active_at: datetime
    archived_at: datetime | None = None
    fork_of: int | None = None
    fork_at_message: int | None = None
    message_count: int = 0
    forge_mode: str | None = None


class DevMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    role: str
    content: list[dict[str, Any]]
    created_at: datetime


class DevMessageCreate(BaseModel):
    text: str = Field(min_length=1)
    images: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class DevForkCreate(BaseModel):
    at_message_id: int


class DevAnnotationCreate(BaseModel):
    url: str | None = None
    note: str = Field(min_length=1)


class DevAnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: int
    url: str | None
    note: str
    created_at: datetime


class DevSessionDetail(BaseModel):
    session: DevSessionRead
    messages: list[DevMessageRead]
    annotations: list[DevAnnotationRead]


class PermissionDecision(BaseModel):
    trust_for_session: bool = False


class FileSearchResult(BaseModel):
    path: str
    name: str
    type: str
