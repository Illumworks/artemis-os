"""Pydantic shape constraints for meeting summarizer LLM output (H4).

The meeting summarizer emits JSON `{bullets, action_items}` that is later read
by Floating Artemis and injected into its system prompt. Without shape
validation, hallucinated action items become durable assistant context — Jon
may be reminded about commitments he never made.

This module enforces shape on the producer side. The Floating Artemis read
site (`artemis/floating_artemis/chat.py`) adds provenance framing so summaries
are treated as LLM inferences rather than verified facts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_LOOSE_DUE_TOKENS = {"today", "tomorrow", "this week", "next week", "tbd"}


class ActionItem(BaseModel):
    """A single action item from a meeting summary."""

    text: str = Field(min_length=1, max_length=500)
    owner: str | None = Field(default=None, max_length=100)
    due: str | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("due")
    @classmethod
    def validate_due_format(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v.lower() in _LOOSE_DUE_TOKENS:
            return v
        try:
            datetime.fromisoformat(v)
            return v
        except ValueError as exc:
            raise ValueError(
                f"Invalid due date format: {v!r}. Use ISO 8601 (YYYY-MM-DD) or one of: "
                "today, tomorrow, this week, next week, TBD."
            ) from exc


class MeetingSummary(BaseModel):
    """LLM-emitted meeting summary shape."""

    bullets: list[str] = Field(default_factory=list, max_length=20)
    action_items: list[ActionItem] = Field(default_factory=list, max_length=15)

    model_config = ConfigDict(extra="forbid")

    @field_validator("bullets")
    @classmethod
    def validate_bullet_length(cls, v: list[str]) -> list[str]:
        for bullet in v:
            if len(bullet.strip()) == 0:
                raise ValueError("Bullet cannot be empty")
            if len(bullet) > 500:
                raise ValueError(f"Bullet exceeds 500 chars: {bullet[:80]!r}...")
        return v


def validate_existing(action_items: Any) -> tuple[bool, str | None]:
    """Audit an existing meeting_summaries.action_items value against new shape.

    Returns (is_valid, error_msg). Used by the existing-rows audit and by the
    Floating Artemis read site to tag legacy rows with `legacy_format=True`.

    Lossless invariant: this function does NOT modify the row — it only reports.
    """
    if action_items is None:
        return (True, None)
    if not isinstance(action_items, list):
        return (False, f"action_items not a list: {type(action_items).__name__}")
    try:
        for item in action_items:
            ActionItem.model_validate(item)
        return (True, None)
    except ValidationError as exc:
        return (False, str(exc))
