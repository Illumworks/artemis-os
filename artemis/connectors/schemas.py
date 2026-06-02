"""Pydantic schemas for the Connectors API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ── Request bodies ─────────────────────────────────────────────────────────────


class ConnectorCreate(BaseModel):
    """Body for POST /api/connectors/."""

    kind: str
    name: str
    credentials: dict[str, str] = Field(default_factory=dict)


class ConnectorUpdate(BaseModel):
    """Body for PATCH /api/connectors/{id}."""

    name: str | None = None
    credentials: dict[str, str] | None = None
    status: str | None = None


class AgentConnectorLink(BaseModel):
    """Body for POST /api/agents/{agent_id}/connectors."""

    connector_id: str
    tool_namespace: str


# ── Response schemas ───────────────────────────────────────────────────────────


class ConnectorOut(BaseModel):
    """Connector detail returned to authenticated owner — includes credentials."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    name: str
    credentials: dict[str, str]  # decrypted for owner; masked otherwise
    status: str
    owner_user_id: int | None = None
    last_validated_at: str | None = None
    created_at: datetime
    updated_at: datetime


class ConnectorListItem(BaseModel):
    """Connector summary for list endpoint — credentials always masked."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: str
    name: str
    status: str
    owner_user_id: int | None = None
    last_validated_at: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentConnectorOut(BaseModel):
    """One agent→connector link as returned by the agent sub-resource."""

    model_config = ConfigDict(from_attributes=True)

    agent_id: int
    connector_id: str
    tool_namespace: str


class ConnectorTestResult(BaseModel):
    """Result from POST /api/connectors/{id}/test."""

    ok: bool
    message: str
