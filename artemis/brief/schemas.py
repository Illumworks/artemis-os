"""Pydantic schemas for the Daily Brief generator (H5).

Every LLM-emitted daily brief must validate against DailyBrief before
being persisted.  Validation failure triggers a single retry;  persistent
failure falls back to an empty DailyBrief() + warning log.

Schema shape (trimmed for scannability):
  - summary:       1-2 sentence opener
  - top_priorities: merged Priorities + Next Actions, max 3 actionable items
  - waiting_on_you: people/threads waiting on Jon
  - okr_at_risk:   1-2 lines for at-risk KRs only (null if none at risk)
  - confidence:    quality signal for the generator
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BriefPriority(BaseModel):
    """One priority/action item — what Jon should act on today."""

    item: str = Field(min_length=1, max_length=300)
    rationale: str | None = Field(default=None, max_length=300)
    urgency: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")


class WaitingItem(BaseModel):
    """One person or thread waiting on Jon."""

    who: str = Field(min_length=1, max_length=150)
    context: str | None = Field(default=None, max_length=300)

    model_config = ConfigDict(extra="forbid")


class DailyBrief(BaseModel):
    """Trimmed daily brief — scannable, no redundant sections.

    Sections:
      summary:        1-2 sentence overall shape of the day.
      top_priorities: Up to 3 actionable items (merged Priorities + Next Actions).
      waiting_on_you: People or threads waiting on Jon.
      okr_at_risk:    1-2 lines on at-risk KRs only; null if nothing at risk.
      confidence:     Reflection of how much the context supports concrete recommendations.
    """

    summary: str | None = Field(default=None, max_length=2000)
    top_priorities: list[BriefPriority] = Field(default_factory=list, max_length=3)
    waiting_on_you: list[WaitingItem] = Field(default_factory=list, max_length=10)
    okr_at_risk: str | None = Field(default=None, max_length=500)
    confidence: Literal["high", "medium", "low"] = "medium"

    model_config = ConfigDict(extra="forbid")
