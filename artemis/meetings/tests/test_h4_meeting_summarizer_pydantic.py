"""H4 — Meeting summarizer Pydantic + Floating Artemis revalidation tests.

Closes the meeting → Floating Artemis pollution chain. Without these shape
constraints, hallucinated action_items become durable Floating Artemis context.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest
from pydantic import ValidationError

from artemis.agent.types import Message, TextBlock, Usage
from artemis.meetings.summary_schemas import (
    ActionItem,
    MeetingSummary,
    validate_existing,
)

# ── Test 1 — valid summary passes ────────────────────────────────────────────


def test_valid_summary_passes_pydantic() -> None:
    raw = json.dumps(
        {
            "bullets": ["Discussed Q2 roadmap", "Confirmed Q3 hiring plan"],
            "action_items": [
                {"text": "Send slides to team", "owner": "Jon", "due": "2026-06-15"},
                {"text": "Follow up with vendor", "owner": None, "due": "this week"},
            ],
        }
    )
    summary = MeetingSummary.model_validate_json(raw)
    assert len(summary.bullets) == 2
    assert len(summary.action_items) == 2
    assert summary.action_items[0].due == "2026-06-15"


# ── Test 2 — invalid due format rejected ─────────────────────────────────────


def test_invalid_due_format_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ActionItem(text="Talk to ops", owner="Jon", due="next Tuesday-ish")
    assert "Invalid due date format" in str(exc_info.value)


# ── Test 3 — empty bullet rejected ───────────────────────────────────────────


def test_empty_bullet_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        MeetingSummary(bullets=["valid bullet", "   "], action_items=[])
    assert "Bullet cannot be empty" in str(exc_info.value)


# ── Test 4 — extra field rejected ────────────────────────────────────────────


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError) as exc_info:
        ActionItem.model_validate(
            {
                "text": "Send recap",
                "owner": "Jon",
                "due": None,
                "hallucinated_field": "I made this up",
            }
        )
    assert (
        "hallucinated_field" in str(exc_info.value).lower()
        or "extra" in str(exc_info.value).lower()
    )


# ── Test 5 — long bullet rejected ────────────────────────────────────────────


def test_bullet_over_500_chars_rejected() -> None:
    long_bullet = "x" * 501
    with pytest.raises(ValidationError):
        MeetingSummary(bullets=[long_bullet], action_items=[])


# ── Test 6 — validation failure triggers retry ───────────────────────────────


class _ScriptedAdapter:
    """Returns scripted responses in order, one per .complete() call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def complete(self, request: Any) -> Any:
        text = self._responses[self.call_count]
        self.call_count += 1
        from artemis.agent.client import CompletionResponse

        return CompletionResponse(
            message=Message(role="assistant", content=[TextBlock(text=text)]),
            stop_reason="end_turn",
            usage=Usage(),
        )


@pytest.mark.asyncio
async def test_validation_failure_triggers_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    # First call: invalid (bad due format). Second call: valid.
    invalid = json.dumps(
        {
            "bullets": ["topic A"],
            "action_items": [{"text": "x", "owner": "Jon", "due": "next Tuesday-ish"}],
        }
    )
    valid = json.dumps(
        {
            "bullets": ["topic A"],
            "action_items": [{"text": "x", "owner": "Jon", "due": "2026-06-15"}],
        }
    )
    scripted = _ScriptedAdapter([invalid, valid])
    monkeypatch.setattr("artemis.providers.get_adapter", lambda *a, **kw: scripted)

    from artemis.meetings.summarizer import _llm_summarize

    bullets_text, action_items = await _llm_summarize(
        "Test meeting", {"transcript": "some content"}
    )
    assert scripted.call_count == 2, "Adapter should be called twice (one retry)"
    assert "topic A" in bullets_text
    assert len(action_items) == 1
    assert action_items[0]["due"] == "2026-06-15"


# ── Test 7 — persistent failure → empty placeholder ──────────────────────────


@pytest.mark.asyncio
async def test_persistent_failure_produces_empty_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = json.dumps(
        {
            "bullets": ["ok"],
            "action_items": [{"text": "x", "due": "garbage date"}],
        }
    )
    scripted = _ScriptedAdapter([invalid, invalid])
    monkeypatch.setattr("artemis.providers.get_adapter", lambda *a, **kw: scripted)

    from artemis.meetings.summarizer import _llm_summarize

    bullets_text, action_items = await _llm_summarize("Bad meeting", {"transcript": "x"})
    # After max_retries (1), returns placeholder, NOT hallucinated content.
    assert scripted.call_count == 2
    assert "validation failed" in bullets_text.lower()
    assert action_items == []


# ── Test 8 — Floating Artemis read-site provenance ───────────────────────────


@pytest.mark.asyncio
async def test_floating_artemis_provenance_block() -> None:
    from datetime import UTC, datetime

    from artemis.floating_artemis.chat import get_recent_summaries_with_provenance

    class _Row:
        granola_id = "g-1"
        title = "Strategy sync"
        summary = "- discussed roadmap\n- agreed on Q3 hires"
        action_items = [{"text": "Send recap", "owner": "Jon", "due": "2026-06-15"}]
        created_at = datetime(2026, 5, 29, 14, 0, tzinfo=UTC)

    async def fake_get_recent_summaries(session: Any, hours: int = 4) -> list[Any]:
        return [_Row()]

    import artemis.floating_artemis.chat as chat_mod
    import artemis.meetings.summarizer as summ_mod

    orig = summ_mod.get_recent_summaries
    summ_mod.get_recent_summaries = fake_get_recent_summaries  # type: ignore[assignment]
    try:
        items = await get_recent_summaries_with_provenance(db_session=object(), hours=4)
    finally:
        summ_mod.get_recent_summaries = orig

    assert len(items) == 1
    item = items[0]
    assert item["title"] == "Strategy sync"
    assert "provenance" in item
    prov = item["provenance"]
    assert prov["source"] == "llm_meeting_summarizer"
    assert prov["transcript_truncated_at_chars"] == 6000
    assert prov["legacy_format"] is False  # valid action items
    assert prov["generated_at"] is not None
    # Spot-check system-prompt framing keywords land in the rendered prompt.
    rendered = chat_mod._build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        recent_meeting_context="You just finished foo.",
        session_id="s-1",
    )
    assert "LLM-generated" in rendered
    assert "treat as inferences" in rendered


# ── Test 9 — existing-rows audit ─────────────────────────────────────────────


def test_validate_existing_handles_legacy_shapes() -> None:
    # None / empty list always valid.
    assert validate_existing(None) == (True, None)
    assert validate_existing([]) == (True, None)
    # Valid item passes.
    assert validate_existing([{"text": "ok", "owner": "Jon", "due": "2026-06-15"}])[0] is True
    # Garbage due rejected.
    ok, err = validate_existing([{"text": "ok", "due": "next Tuesday-ish"}])
    assert ok is False
    assert err is not None
    # Extra field rejected.
    ok, _ = validate_existing([{"text": "ok", "owner": "Jon", "due": None, "hallucinated": "x"}])
    assert ok is False


@pytest.mark.asyncio
@pytest.mark.skipif(
    "artemis_test" not in os.environ.get("ARTEMIS_DB_URL", ""),
    reason="needs test DB",
)
async def test_existing_rows_audit_counts_invalid() -> None:
    """Run the existing-rows audit against the live test DB and report counts."""
    from sqlalchemy import select

    import artemis.db as _db
    from artemis.meetings.models import MeetingSummary as MeetingSummaryRow

    invalid_count = 0
    total = 0
    async with _db.SessionLocal() as session:
        result = await session.execute(select(MeetingSummaryRow))
        for row in result.scalars().all():
            total += 1
            ok, _ = validate_existing(row.action_items)
            if not ok:
                invalid_count += 1
    # Always passes; just records the count for the report.
    print(f"\n[H4 audit] total={total} invalid_action_items={invalid_count}")
    assert invalid_count >= 0
