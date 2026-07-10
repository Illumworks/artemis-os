"""Pydantic models for the Agent Report Card.

House style: Pydantic for artifact/report surfaces (see memory/eval/runner.py),
dataclasses only on the hot path. Everything here is serialized to disk, so
Pydantic buys validation on both write and re-read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SCORE_MIN = 1
SCORE_MAX = 5


# ── Rubrics ───────────────────────────────────────────────────────────────────


class RubricCriterion(BaseModel):
    """One gradeable dimension. ``guidance`` tells the judge what 1 vs 5 means."""

    id: str
    name: str
    guidance: str
    weight: float = Field(default=1.0, gt=0)


class Rubric(BaseModel):
    rubric_id: str
    agent_id: str
    version: int = 1
    description: str
    criteria: list[RubricCriterion]

    @field_validator("criteria")
    @classmethod
    def _criteria_nonempty_unique(cls, value: list[RubricCriterion]) -> list[RubricCriterion]:
        if not value:
            raise ValueError("rubric must define at least one criterion")
        ids = [c.id for c in value]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate criterion ids: {ids}")
        return value

    def criterion_ids(self) -> list[str]:
        return [c.id for c in self.criteria]


# ── Eval cases ────────────────────────────────────────────────────────────────


class TranscriptTurn(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    text: str


class EvalCase(BaseModel):
    """One graded unit: the context the agent saw + what the agent produced.

    ``source`` distinguishes curated fixtures from captured production turns —
    the plug-in point for grading real outputs later is simply constructing
    EvalCase objects with ``source="captured"`` and passing them to
    ``grade_cases`` / ``run_report_card``.
    """

    case_id: str
    agent_id: str
    description: str = ""
    input_transcript: list[TranscriptTurn]
    agent_output: str
    tool_calls: list[str] = Field(
        default_factory=list,
        description=(
            "Names of tools the agent ACTUALLY invoked while producing this "
            "output. Load-bearing for acts-vs-narrates criteria: the judge is "
            "told to trust this list over the agent's own prose."
        ),
    )
    tags: list[str] = Field(default_factory=list)
    source: Literal["fixture", "captured"] = "fixture"


# ── Judge output (per case) ──────────────────────────────────────────────────


class CriterionScore(BaseModel):
    criterion_id: str
    score: int = Field(ge=SCORE_MIN, le=SCORE_MAX)
    justification: str = ""


class CaseGrade(BaseModel):
    """Grades for one case. ``error`` is set (and scores empty) when the judge
    output could not be parsed even after retry — fail-safe, never raises out
    of the harness loop."""

    case_id: str
    agent_id: str
    rubric_id: str
    scores: list[CriterionScore] = Field(default_factory=list)
    overall: float | None = None
    """Weighted mean of criterion scores. Computed by us, not the judge."""
    judge_comment: str = ""
    missing_criteria: list[str] = Field(default_factory=list)
    error: str | None = None
    judge_provider: str | None = None
    judge_model: str | None = None
    graded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.scores)


# ── Aggregated report ────────────────────────────────────────────────────────


class CriterionSummary(BaseModel):
    criterion_id: str
    name: str
    mean: float
    min: int
    max: int
    count: int


class AgentSummary(BaseModel):
    agent_id: str
    rubric_id: str
    case_count: int
    graded_count: int
    failed_count: int
    overall_mean: float | None
    criteria: list[CriterionSummary]
    failed_case_ids: list[str] = Field(default_factory=list)


class ReportCardRun(BaseModel):
    run_id: str
    label: str
    judge_provider: str | None
    judge_model: str | None
    created_at: datetime
    agents: list[AgentSummary]
    grades: list[CaseGrade]
    notes: list[str] = Field(default_factory=list)
