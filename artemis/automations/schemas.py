"""Pydantic 2.x schemas for the Automations domain (OP1)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AutomationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str | None = None
    status: str = "active"
    trigger_type: str = Field(default="manual", alias="triggerType")
    schedule_config: dict[str, Any] | None = Field(default=None, alias="scheduleConfig")
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    model: str | None = None
    provider: str | None = None
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    approval_policy: dict[str, Any] | None = Field(default=None, alias="approvalPolicy")
    output_config: dict[str, Any] | None = Field(default=None, alias="outputConfig")
    metadata: dict[str, Any] | None = None
    owner_user_id: str | None = Field(default=None, alias="ownerUserId")


class AutomationUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    description: str | None = None
    status: str | None = None
    trigger_type: str | None = Field(default=None, alias="triggerType")
    schedule_config: dict[str, Any] | None = Field(default=None, alias="scheduleConfig")
    target_type: str | None = Field(default=None, alias="targetType")
    target_id: str | None = Field(default=None, alias="targetId")
    model: str | None = None
    provider: str | None = None
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    approval_policy: dict[str, Any] | None = Field(default=None, alias="approvalPolicy")
    output_config: dict[str, Any] | None = Field(default=None, alias="outputConfig")
    metadata: dict[str, Any] | None = None


class AutomationRunRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    automation_id: str = Field(alias="automationId")
    status: str
    trigger: str
    triggered_by: str | None = Field(default=None, alias="triggeredBy")
    started_at: datetime = Field(alias="startedAt")
    completed_at: datetime | None = Field(default=None, alias="completedAt")
    target_run_id: str | None = Field(default=None, alias="targetRunId")
    error_message: str | None = Field(default=None, alias="errorMessage")
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(alias="createdAt")


def automation_run_to_schema(run: Any) -> AutomationRunRead:
    """Build an AutomationRunRead from an ORM row."""
    return AutomationRunRead(
        id=run.id,
        automation_id=run.automation_id,
        status=run.status,
        trigger=run.trigger,
        triggered_by=run.triggered_by,
        started_at=run.started_at,
        completed_at=run.completed_at,
        target_run_id=run.target_run_id,
        error_message=run.error_message,
        metadata=run.metadata_,
        created_at=run.created_at,
    )


class AutomationRead(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    description: str | None = None
    status: str
    trigger_type: str = Field(alias="triggerType")
    schedule_config: dict[str, Any] | None = Field(default=None, alias="scheduleConfig")
    target_type: str = Field(alias="targetType")
    target_id: str = Field(alias="targetId")
    model: str | None = None
    provider: str | None = None
    fallback_provider: str | None = Field(default=None, alias="fallbackProvider")
    fallback_model: str | None = Field(default=None, alias="fallbackModel")
    approval_policy: dict[str, Any] | None = Field(default=None, alias="approvalPolicy")
    output_config: dict[str, Any] | None = Field(default=None, alias="outputConfig")
    metadata: dict[str, Any] | None = None
    owner_user_id: str | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    latest_run: AutomationRunRead | None = Field(default=None, alias="latestRun")


def automation_to_schema(a: Any, latest_run: Any | None = None) -> AutomationRead:
    """Build an AutomationRead from an ORM row, optionally embedding the latest run."""
    return AutomationRead(
        id=a.id,
        name=a.name,
        description=a.description,
        status=a.status,
        trigger_type=a.trigger_type,
        schedule_config=a.schedule_config,
        target_type=a.target_type,
        target_id=a.target_id,
        model=a.model,
        provider=a.provider,
        fallback_provider=a.fallback_provider,
        fallback_model=a.fallback_model,
        approval_policy=a.approval_policy,
        output_config=a.output_config,
        metadata=a.metadata_,
        owner_user_id=a.owner_user_id,
        created_at=a.created_at,
        updated_at=a.updated_at,
        archived_at=a.archived_at,
        latest_run=automation_run_to_schema(latest_run) if latest_run else None,
    )


class RunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    triggered_by: str | None = Field(default=None, alias="triggeredBy")
    metadata: dict[str, Any] | None = None
