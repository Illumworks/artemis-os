"""Pydantic schemas for trajectory summarizer output (H3).

Validates and bounds the JSON emitted by the trajectory_summarizer LLM before
it is written to agent_run_trajectory_summaries.  This is a producer-side
guard — it does not alter existing rows.

Shape matches the prompt contract exactly: three nullable one-sentence fields
plus optional confidence/evidence provenance markers for future use.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TrajectorySummary(BaseModel):
    """Bounded Pydantic model for trajectory summarizer output.

    All three text fields are capped at 2000 chars (one sentence is ~100-200).
    extra="forbid" rejects hallucinated fields that would silently pollute the
    Builder's self-improvement context.
    """

    what_worked: str | None = Field(default=None, max_length=2000)
    what_stalled: str | None = Field(default=None, max_length=2000)
    what_was_missing: str | None = Field(default=None, max_length=2000)

    # Provenance / confidence markers — future-proofing.
    confidence: Literal["high", "medium", "low"] = "medium"
    evidence_tool_calls: list[str] = Field(default_factory=list)
    evidence_signal_ids: list[int] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")
