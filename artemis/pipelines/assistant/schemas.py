"""Pydantic schemas for the Pipeline AI Panel (H5).

PROPOSAL_BEGIN...PROPOSAL_END blocks extracted from assistant text are
validated against PipelineProposal before being surfaced to the UI.
Validation failure strips the malformed block from the response text and
logs a warning — no auto-retry (the operator's next turn is the natural
retry path).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PipelineNodeMod(BaseModel):
    """One modification to a pipeline node."""

    action: Literal["add", "update", "remove"]
    node_id: str = Field(min_length=1, max_length=100)
    node_type: str | None = Field(default=None, max_length=100)
    config: dict[str, Any] | None = None

    model_config = ConfigDict(extra="forbid")


class PipelineEdgeMod(BaseModel):
    """One modification to a pipeline edge."""

    action: Literal["add", "remove"]
    from_node: str = Field(min_length=1, max_length=100)
    to_node: str = Field(min_length=1, max_length=100)
    condition: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


class PipelineProposal(BaseModel):
    """Inline proposal block emitted by the Pipeline AI Panel.

    Strict shape — extra fields forbidden.  Malformed proposals are stripped
    from the assistant response rather than surfaced to the UI.
    """

    summary: str = Field(min_length=1, max_length=500)
    node_mods: list[PipelineNodeMod] = Field(default_factory=list, max_length=20)
    edge_mods: list[PipelineEdgeMod] = Field(default_factory=list, max_length=30)
    rationale: str | None = Field(default=None, max_length=1500)
    confidence: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")
