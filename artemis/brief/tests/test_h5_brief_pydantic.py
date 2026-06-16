"""H5 — Daily Brief Pydantic + retry tests.

Test plan:
1. Valid brief passes Pydantic (new trimmed schema).
2. Oversized priority item rejected.
3. Extra field rejected.
4. Invalid urgency rejected.
5. Empty priority item rejected.
6. Retry on validation failure — second call succeeds.
7. Persistent failure produces empty DailyBrief.
8. _build_prompt references every DailyBrief field name.
9. _build_context_string carries Jira ticket titles.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from pydantic import ValidationError

from artemis.brief.schemas import (
    BriefPriority,
    DailyBrief,
    WaitingItem,
)

# ── Fixture payloads ──────────────────────────────────────────────────────────

_VALID_BRIEF_DICT: dict[str, Any] = {
    "summary": "Focus day — sprint review then OKR update.",
    "top_priorities": [
        {"item": "Complete sprint review", "rationale": "Due this week", "urgency": "high"},
        {"item": "Update OKR progress", "rationale": None, "urgency": "medium"},
        {"item": "Reply to Angela re: pipeline", "rationale": "Blocked downstream", "urgency": "medium"},
    ],
    "waiting_on_you": [
        {"who": "Angela", "context": "Waiting on pipeline approval"},
    ],
    "okr_at_risk": "Q2 Product Adoption KR at 34% — stalled",
    "confidence": "high",
}


# ── Test 1: Valid brief passes Pydantic ───────────────────────────────────────


def test_valid_brief_passes_pydantic() -> None:
    """DailyBrief.model_validate accepts a canonical valid brief."""
    brief = DailyBrief.model_validate(_VALID_BRIEF_DICT)
    assert len(brief.top_priorities) == 3
    assert brief.top_priorities[0].urgency == "high"
    assert brief.confidence == "high"
    assert brief.summary == "Focus day — sprint review then OKR update."


def test_valid_brief_passes_model_validate_json() -> None:
    """DailyBrief.model_validate_json also accepts a canonical valid brief."""
    raw = json.dumps(_VALID_BRIEF_DICT)
    brief = DailyBrief.model_validate_json(raw)
    assert len(brief.waiting_on_you) == 1
    assert brief.waiting_on_you[0].who == "Angela"


def test_valid_brief_empty_optional_fields() -> None:
    """DailyBrief accepts empty optional fields (no waiting, no okr_at_risk)."""
    brief = DailyBrief.model_validate({
        "summary": "Quiet day.",
        "top_priorities": [],
        "waiting_on_you": [],
        "okr_at_risk": None,
        "confidence": "low",
    })
    assert brief.summary == "Quiet day."
    assert brief.top_priorities == []
    assert brief.okr_at_risk is None


# ── Test 2: Oversized priority item rejected ──────────────────────────────────


def test_oversized_priority_item_rejected() -> None:
    """BriefPriority rejects an item string longer than 300 chars."""
    with pytest.raises(ValidationError) as exc_info:
        BriefPriority(item="x" * 301)
    assert "item" in str(exc_info.value).lower() or "300" in str(exc_info.value)


def test_oversized_priority_item_rejected_via_model() -> None:
    """DailyBrief rejects nested priority with oversized item."""
    bad = {**_VALID_BRIEF_DICT, "top_priorities": [{"item": "x" * 301, "urgency": "high"}]}
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
    """BriefPriority rejects extra fields (extra='forbid')."""
    with pytest.raises(ValidationError) as exc_info:
        BriefPriority.model_validate({"item": "Valid item", "hallucinated_key": "oops"})
    assert (
        "hallucinated_key" in str(exc_info.value).lower() or "extra" in str(exc_info.value).lower()
    )


def test_extra_field_rejected_in_waiting_item() -> None:
    """WaitingItem rejects extra fields (extra='forbid')."""
    with pytest.raises(ValidationError) as exc_info:
        WaitingItem.model_validate({"who": "Alice", "hallucinated_key": "oops"})
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


def test_empty_who_rejected() -> None:
    """WaitingItem rejects an empty who string (min_length=1)."""
    with pytest.raises(ValidationError):
        WaitingItem(who="")


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
    assert len(brief.top_priorities) == 3
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
    assert brief.top_priorities == []
    assert brief.waiting_on_you == []
    assert brief.summary is None
    # Warning was logged
    assert any(
        "validation" in rec.message.lower() or "persistent" in rec.message.lower()
        for rec in caplog.records
        if rec.levelno >= logging.WARNING
    ), f"Expected warning log, got: {[r.message for r in caplog.records]}"


# ── Test 8: _build_prompt references new DailyBrief fields ────────────────────


def test_build_prompt_references_dailybrief_schema() -> None:
    """The LLM prompt must reference every DailyBrief field name + enum value,
    and must not reference the legacy fields (highlights, next_actions, risks,
    okr_status, headline, continuity, defer, slackUrgency, calendarNote)."""
    from artemis.brief.prompt import _build_prompt

    prompt = _build_prompt("today is test day")

    for field in (
        '"summary"',
        '"top_priorities"',
        '"waiting_on_you"',
        '"okr_at_risk"',
        '"confidence"',
        '"item"',
        '"rationale"',
        '"urgency"',
        '"who"',
        '"context"',
    ):
        assert field in prompt, f"DailyBrief field {field} missing from prompt"

    for legacy in (
        '"highlights"',
        '"next_actions"',
        '"risks"',
        '"headline"',
        '"continuity"',
        '"defer"',
        '"slackUrgency"',
        '"calendarNote"',
        '"rank"',
        '"ticket"',
        '"why"',
    ):
        assert legacy not in prompt, f"Legacy field {legacy} still in prompt"

    for enum_value in ('"high"', '"medium"', '"low"'):
        assert enum_value in prompt, f"Enum value {enum_value} missing from prompt"

    # Cleanup 3: prompt must instruct the LLM to include ticket titles, not bare keys
    assert "ticket title" in prompt.lower() or "title" in prompt.lower()
    assert "bare jira key" in prompt.lower() or "bare" in prompt.lower()


# ── Test 9: _build_context_string carries Jira ticket titles ─────────────────


def test_build_context_string_includes_jira_titles() -> None:
    """Context fed to the LLM must include ticket title (not just bare key).

    The Jira column items use the key 'title' (from _map_column_item).
    _build_context_string must read 'title' so the LLM can refer to it.
    """
    from artemis.brief.prompt import _build_context_string

    sources = {
        "jira": {
            "connected": True,
            "columns": [
                {
                    "key": "prog",
                    "items": [
                        {"key": "MT-456", "title": "Fix login redirect", "priority": "High"},
                        {"key": "MT-551", "title": "Update email templates", "priority": "Medium"},
                    ],
                },
                {"key": "review", "items": []},
                {"key": "blocked", "items": []},
            ],
        },
    }

    ctx = _build_context_string(sources)

    # Titles must appear in the context
    assert "Fix login redirect" in ctx
    assert "Update email templates" in ctx

    # Keys should also appear (for full KEY — Title references)
    assert "MT-456" in ctx
    assert "MT-551" in ctx


def test_build_context_string_jira_falls_back_to_summary_key() -> None:
    """If items use 'summary' instead of 'title', context builder still works."""
    from artemis.brief.prompt import _build_context_string

    sources = {
        "jira": {
            "connected": True,
            "columns": [
                {
                    "key": "prog",
                    "items": [
                        {"key": "MT-100", "summary": "Legacy summary field", "priority": "Low"},
                    ],
                },
                {"key": "review", "items": []},
                {"key": "blocked", "items": []},
            ],
        },
    }

    ctx = _build_context_string(sources)

    assert "Legacy summary field" in ctx
    assert "MT-100" in ctx


def test_build_prompt_instructs_no_bare_keys() -> None:
    """Prompt must contain a CRITICAL rule forbidding bare Jira keys."""
    from artemis.brief.prompt import _build_prompt

    prompt = _build_prompt("today is test day")

    # The rule must be explicit and marked as critical
    assert "CRITICAL" in prompt
    assert "bare" in prompt.lower()
    # Must tell LLM to include the title alongside the key
    assert "title" in prompt.lower()
