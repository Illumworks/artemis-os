"""Pydantic 2.x DTOs for the OKR Studio domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        from_attributes=True,
    )


# ── OKR Key Result ────────────────────────────────────────────────────────────


class OkrKeyResultCreate(_Base):
    objective_id: int = Field(..., alias="objectiveId")
    title: str
    prog: int = 0
    status: str = "notstarted"
    done_bullets: list[Any] = Field(default_factory=list, alias="doneBullets")
    gaps_bullets: list[Any] = Field(default_factory=list, alias="gapsBullets")
    note: str | None = None
    sort_order: int = Field(default=0, alias="sortOrder")
    target_text: str | None = Field(default=None, alias="targetText")


class OkrKeyResultRead(_Base):
    id: int
    objective_id: int = Field(alias="objectiveId")
    title: str
    prog: int
    status: str
    done_bullets: list[Any] = Field(alias="doneBullets")
    gaps_bullets: list[Any] = Field(alias="gapsBullets")
    note: str | None = None
    sort_order: int = Field(alias="sortOrder")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    archive_reason: str | None = Field(default=None, alias="archiveReason")
    source_year: int | None = Field(default=None, alias="sourceYear")
    target_text: str | None = Field(default=None, alias="targetText")
    updated_at: datetime = Field(alias="updatedAt")


class OkrKeyResultUpdate(_Base):
    title: str | None = None
    prog: int | None = None
    status: str | None = None
    done_bullets: list[Any] | None = Field(default=None, alias="doneBullets")
    gaps_bullets: list[Any] | None = Field(default=None, alias="gapsBullets")
    note: str | None = None
    sort_order: int | None = Field(default=None, alias="sortOrder")
    target_text: str | None = Field(default=None, alias="targetText")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    archive_reason: str | None = Field(default=None, alias="archiveReason")


# ── OKR Objective ─────────────────────────────────────────────────────────────


class OkrObjectiveCreate(_Base):
    title: str
    description: str | None = (
        None  # stored as 'description' in PG (Node used 'desc', a PG reserved word)
    )
    progress: int = 0
    tone: str = "sage"
    owner: str | None = None
    weight: str | None = None
    cycle: str | None = None
    sort_order: int = Field(default=0, alias="sortOrder")
    rolls_up_to: str | None = Field(default=None, alias="rollsUpTo")
    source_year: int | None = Field(default=None, alias="sourceYear")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")


class OkrObjectiveRead(_Base):
    id: int
    title: str
    description: str | None = None
    progress: int
    tone: str
    owner: str | None = None
    weight: str | None = None
    cycle: str | None = None
    sort_order: int = Field(alias="sortOrder")
    rolls_up_to: str | None = Field(default=None, alias="rollsUpTo")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    archive_reason: str | None = Field(default=None, alias="archiveReason")
    source_year: int | None = Field(default=None, alias="sourceYear")
    owner_user_id: int | None = Field(default=None, alias="ownerUserId")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    key_results: list[OkrKeyResultRead] = Field(default_factory=list, alias="keyResults")


class OkrObjectiveUpdate(_Base):
    title: str | None = None
    description: str | None = None
    progress: int | None = None
    tone: str | None = None
    owner: str | None = None
    weight: str | None = None
    cycle: str | None = None
    sort_order: int | None = Field(default=None, alias="sortOrder")
    rolls_up_to: str | None = Field(default=None, alias="rollsUpTo")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    archive_reason: str | None = Field(default=None, alias="archiveReason")
    source_year: int | None = Field(default=None, alias="sourceYear")


# ── OKR Activity ──────────────────────────────────────────────────────────────


class OkrActivityCreate(_Base):
    text: str
    kr_id: int | None = Field(default=None, alias="krId")
    kr_label: str | None = Field(default=None, alias="krLabel")
    raw_text: str | None = Field(default=None, alias="rawText")


class OkrActivityRead(_Base):
    id: int
    text: str
    kr_id: int | None = Field(default=None, alias="krId")
    kr_label: str | None = Field(default=None, alias="krLabel")
    raw_text: str | None = Field(default=None, alias="rawText")
    mapping_confidence: float | None = Field(default=None, alias="mappingConfidence")
    cleaned_at: datetime | None = Field(default=None, alias="cleanedAt")
    created_at: datetime = Field(alias="createdAt")


# ── OKR Next Up ───────────────────────────────────────────────────────────────


class OkrNextUpCreate(_Base):
    ref: str = "—"
    text: str
    prio: str = "med"
    sort_order: int = Field(default=0, alias="sortOrder")
    source: str = "manual"
    action_type: str = Field(default="advice", alias="actionType")
    dispatch_target: str | None = Field(default=None, alias="dispatchTarget")
    dispatch_params: dict[str, Any] | None = Field(default=None, alias="dispatchParams")
    rationale: str | None = None


class OkrNextUpRead(_Base):
    id: int
    ref: str
    text: str
    prio: str
    sort_order: int = Field(alias="sortOrder")
    dismissed_at: datetime | None = Field(default=None, alias="dismissedAt")
    source: str
    action_type: str = Field(alias="actionType")
    dispatch_target: str | None = Field(default=None, alias="dispatchTarget")
    dispatch_params: dict[str, Any] | None = Field(default=None, alias="dispatchParams")
    generated_at: datetime | None = Field(default=None, alias="generatedAt")
    rationale: str | None = None


class OkrNextUpUpdate(_Base):
    ref: str | None = None
    text: str | None = None
    prio: str | None = None
    sort_order: int | None = Field(default=None, alias="sortOrder")
    dismissed_at: datetime | None = Field(default=None, alias="dismissedAt")


# ── Migration / dry-run DTOs (used by scripts/migrate_okr_writing_rules.py) ──


class OkrObjectiveRow(_Base):
    """Validates a raw SQLite row for okr_objectives before migration."""

    id: int
    title: str
    desc: str | None = None
    progress: int = 0
    tone: str = "sage"
    owner: str | None = None
    weight: str | None = None
    cycle: str | None = None
    sort_order: int = 0
    rolls_up_to: str | None = None
    archived_at: int | None = None  # unix seconds in source
    archive_reason: str | None = None
    source_year: int | None = None
    created_at: int | None = None
    updated_at: int | None = None


class OkrKeyResultRow(_Base):
    """Validates a raw SQLite row for okr_key_results before migration."""

    id: int
    objective_id: int
    title: str
    prog: int = 0
    status: str = "notstarted"
    done_bullets: str = "[]"  # JSON-in-TEXT in source
    gaps_bullets: str = "[]"
    note: str | None = None
    sort_order: int = 0
    archived_at: int | None = None
    archive_reason: str | None = None
    source_year: int | None = None
    target_text: str | None = None
    updated_at: int | None = None


class OkrActivityRow(_Base):
    """Validates a raw SQLite row for okr_activity before migration."""

    id: int
    text: str
    kr_id: int | None = None
    kr_label: str | None = None
    raw_text: str | None = None
    mapping_confidence: float | None = None
    cleaned_at: int | None = None
    created_at: int | None = None


class OkrNextUpRow(_Base):
    """Validates a raw SQLite row for okr_next_up before migration."""

    id: int
    ref: str = "—"
    text: str
    prio: str = "med"
    sort_order: int = 0
    dismissed_at: int | None = None
    source: str = "manual"
    action_type: str = "advice"
    dispatch_target: str | None = None
    dispatch_params: str | None = None  # JSON-in-TEXT
    generated_at: int | None = None
    rationale: str | None = None


class OkrUpdatePreviewRow(_Base):
    """Validates a raw SQLite row for okr_update_previews before migration."""

    id: int
    created_at: int | None = None
    raw_input: str | None = None
    input_format: str = "text"
    diff_json: str | None = None  # JSON-in-TEXT
    committed_at: int | None = None
