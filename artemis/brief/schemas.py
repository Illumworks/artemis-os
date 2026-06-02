"""Pydantic schemas for the Daily Brief generator (H5).

Every LLM-emitted daily brief must validate against DailyBrief before
being persisted.  Validation failure triggers a single retry;  persistent
failure falls back to an empty DailyBrief() + warning log.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BriefHighlight(BaseModel):
    """One highlight item in the daily brief."""

    title: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, max_length=100)  # 'jira', 'calendar', 'okr', etc.

    model_config = ConfigDict(extra="forbid")


class BriefPriority(BaseModel):
    """One priority item — what the user should focus on."""

    item: str = Field(min_length=1, max_length=300)
    rationale: str | None = Field(default=None, max_length=500)
    urgency: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")


class BriefNextAction(BaseModel):
    """One concrete next action."""

    action: str = Field(min_length=1, max_length=300)
    owner: str | None = Field(default=None, max_length=100)
    due: str | None = Field(default=None, max_length=50)  # ISO date or loose token

    model_config = ConfigDict(extra="forbid")


class DailyBrief(BaseModel):
    """Full daily brief output.  Strict shape — extra fields forbidden."""

    highlights: list[BriefHighlight] = Field(default_factory=list, max_length=10)
    priorities: list[BriefPriority] = Field(default_factory=list, max_length=8)
    next_actions: list[BriefNextAction] = Field(default_factory=list, max_length=10)
    okr_status: str | None = Field(default=None, max_length=500)
    risks: list[str] = Field(default_factory=list, max_length=10)
    summary: str | None = Field(default=None, max_length=2000)
    confidence: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")
