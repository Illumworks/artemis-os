"""H5 — Daily Brief Pydantic + retry tests.

Test plan:
1. Valid brief passes Pydantic.
2. Oversized highlight title rejected.
3. Extra field rejected.
4. Invalid urgency rejected.
5. Empty priority item rejected.
6. Retry on validation failure — second call succeeds.
7. Persistent failure produces empty DailyBrief.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from pydantic import ValidationError

from artemis.brief.schemas import (
    BriefHighlight,
    BriefNextAction,
    BriefPriority,
    DailyBrief,
)

# ── Fixture payloads ──────────────────────────────────────────────────────────

_VALID_BRIEF_DICT: dict[str, Any] = {
    "highlights": [
        {"title": "Q3 roadmap aligned", "detail": "Team agreed on priorities", "source": "jira"},
    ],
    "priorities": [
        {"item": "Complete sprint review", "rationale": "Due this week", "urgency": "high"},
        {"item": "Update OKR progress", "urgency": "medium"},
    ],
    "next_actions": [
        {"action": "Send meeting notes", "owner": "Jon", "due": "2026-06-01"},
    ],
    "okr_status": "Q2 at 72%, on track",
    "risks": ["Vendor delivery slipping"],
    "summary": "Focus day — sprint review + OKR update.",
    "confidence": "high",
}


# ── Test 1: Valid brief passes Pydantic ───────────────────────────────────────


def test_valid_brief_passes_pydantic() -> None:
    """DailyBrief.model_validate accepts a canonical valid brief."""
    brief = DailyBrief.model_validate(_VALID_BRIEF_DICT)
    assert len(brief.highlights) == 1
    assert brief.highlights[0].title == "Q3 roadmap aligned"
    assert len(brief.priorities) == 2
    assert brief.priorities[0].urgency == "high"
    assert brief.confidence == "high"
    assert brief.summary == "Focus day — sprint review + OKR update."


def test_valid_brief_passes_model_validate_json() -> None:
    """DailyBrief.model_validate_json also accepts a canonical valid brief."""
    raw = json.dumps(_VALID_BRIEF_DICT)
    brief = DailyBrief.model_validate_json(raw)
    assert len(brief.next_actions) == 1
    assert brief.next_actions[0].action == "Send meeting notes"


# ── Test 2: Oversized highlight title rejected ────────────────────────────────


def test_oversized_highlight_title_rejected() -> None:
    """BriefHighlight rejects a title longer than 200 chars."""
    with pytest.raises(ValidationError) as exc_info:
        BriefHighlight(title="x" * 201, detail=None, source=None)
    assert "title" in str(exc_info.value).lower() or "200" in str(exc_info.value)


def test_oversized_highlight_title_rejected_via_model() -> None:
    """DailyBrief rejects nested highlight with oversized title."""
    bad = {**_VALID_BRIEF_DICT, "highlights": [{"title": "x" * 201}]}
    with pytest.raises(ValidationError):
        DailyBrief.model_validate(bad)


# ── Test 3: Extra field rejected ──────────────────────────────────────────────


def test_extra_field_rejected_at_root() -> None:
    """DailyBrief rejects extra fields at root level (extra='forbid')."""
    with pytest.raises(ValidationError) as exc_info:
        DailyBrief.model_validate({**_VALID_BRIEF_DICT, "hallucinated_field": "I made this up"})
    assert (
        "hallucinated_field" in str(exc_info.value).lower()
        or "extra" in str(exc_info.value).lower()
    )


def test_extra_field_rejected_in_sub_model() -> None:
    """BriefHighlight rejects extra fields (extra='forbid')."""
    with pytest.raises(ValidationError) as exc_info:
        BriefHighlight.model_validate({"title": "Valid title", "hallucinated_key": "oops"})
    assert (
        "hallucinated_key" in str(exc_info.value).lower() or "extra" in str(exc_info.value).lower()
    )


# ── Test 4: Invalid urgency rejected ──────────────────────────────────────────


def test_invalid_urgency_rejected() -> None:
    """BriefPriority rejects urgency values not in the Literal."""
    with pytest.raises(ValidationError) as exc_info:
        BriefPriority(item="Do something", urgency="extreme")
    assert "urgency" in str(exc_info.value).lower() or "extreme" in str(exc_info.value)


def test_invalid_confidence_rejected() -> None:
    """DailyBrief rejects confidence values not in the Literal."""
    with pytest.raises(ValidationError):
        DailyBrief.model_validate({**_VALID_BRIEF_DICT, "confidence": "very_high"})


# ── Test 5: Empty priority item rejected ──────────────────────────────────────


def test_empty_priority_item_rejected() -> None:
    """BriefPriority rejects an empty item string (min_length=1)."""
    with pytest.raises(ValidationError) as exc_info:
        BriefPriority(item="")
    assert "item" in str(exc_info.value).lower() or "min_length" in str(exc_info.value).lower()


def test_empty_action_rejected() -> None:
    """BriefNextAction rejects an empty action string (min_length=1)."""
    with pytest.raises(ValidationError):
        BriefNextAction(action="")


# ── Scripted adapter helper ───────────────────────────────────────────────────


class _ScriptedAdapter:
    """Returns scripted LLM text responses in order, one per .complete() call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def complete(self, request: Any) -> Any:
        from artemis.agent.client import CompletionResponse
        from artemis.agent.types import Message, TextBlock, Usage

        text = self._responses[self.call_count % len(self._responses)]
        self.call_count += 1
        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=text)]),
            stop_reason="end_turn",
            usage=Usage(input_tokens=50, output_tokens=100),
        )


def _make_adapter(responses: list[str]) -> _ScriptedAdapter:
    return _ScriptedAdapter(responses)


# ── Test 6: Retry on validation failure ───────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_on_validation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock adapter returns invalid JSON first, valid DailyBrief JSON second.

    Verifies that _generate_with_retry calls the adapter twice and returns
    the valid result from the second attempt.
    """
    invalid_json = '{"urgency": "extreme", "hallucinated": true}'  # fails DailyBrief validation
    valid_json = json.dumps(_VALID_BRIEF_DICT)

    scripted = _ScriptedAdapter([invalid_json, valid_json])
    monkeypatch.setattr("artemis.brief.generator._resolve_adapter", lambda: (scripted, "mock"))

    from artemis.brief.generator import _generate_with_retry
    from artemis.brief.prompt import _build_prompt

    brief, model_used, _, _ = await _generate_with_retry(_build_prompt("today is test day"))

    assert scripted.call_count == 2, f"Expected 2 adapter calls, got {scripted.call_count}"
    assert len(brief.highlights) == 1
    assert brief.confidence == "high"


# ── Test 7: Persistent failure produces empty DailyBrief ─────────────────────


@pytest.mark.asyncio
async def test_persistent_failure_produces_empty_brief(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mock adapter always returns invalid JSON.

    Verifies that after max_retries=1 the helper returns an empty DailyBrief()
    and logs a warning.
    """
    always_bad = '{"urgency": "extreme", "hallucinated": true}'  # never valid

    scripted = _ScriptedAdapter([always_bad, always_bad])
    monkeypatch.setattr("artemis.brief.generator._resolve_adapter", lambda: (scripted, "mock"))

    from artemis.brief.generator import _generate_with_retry
    from artemis.brief.prompt import _build_prompt

    with caplog.at_level(logging.WARNING, logger="artemis.brief.generator"):
        brief, _, _, _ = await _generate_with_retry(_build_prompt("today is test day"))

    assert scripted.call_count == 2, f"Expected 2 adapter calls, got {scripted.call_count}"
    # Returns empty DailyBrief with defaults
    assert isinstance(brief, DailyBrief)
    assert brief.highlights == []
    assert brief.priorities == []
    assert brief.summary is None
    # Warning was logged
    assert any(
        "validation" in rec.message.lower() or "persistent" in rec.message.lower()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    ), f"Expected warning log, got: {[r.message for r in caplog.records]}"
