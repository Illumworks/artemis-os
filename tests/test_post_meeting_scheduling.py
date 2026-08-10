"""Unit tests for post-meeting scheduling (v1).

These are PURE-FUNCTION tests — no DB, no network, no live LLM. They cover:
  - detection JSON parsing + confidence gate (with a fake adapter)
  - timeframe → search-window resolution
  - free-slot finding (working hours, weekend skip, conflict avoidance)
  - free/busy parsing INCLUDING the key coworker-unreadable degradation
  - attendee email/name split
  - proposal rendering (slots + caveats, no buttons)
  - calendar.create payload shape for the agency gate

The DB/Slack/GCal orchestration (run_post_meeting_scheduling_sweep) is
live-verified by the Lead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from artemis.proactivity.post_meeting_scheduling import (
    BusyInterval,
    CandidateSlot,
    SchedulingIntent,
    SlotProposal,
    build_calendar_create_payload,
    build_detection_prompt,
    build_proposal_preview,
    classify_action_item,
    find_free_slots,
    format_proposal_message,
    format_slot_label,
    parse_detection_response,
    parse_freebusy_response,
    resolve_search_window,
    scheduled_dedup_key,
    split_resolved_attendees,
)

TZ = ZoneInfo("America/Chicago")
TZ_NAME = "America/Chicago"


# ── Detection parsing ─────────────────────────────────────────────────────────


def test_parse_detection_clean_json():
    raw = (
        '{"is_scheduling": true, "title": "Writing Studio training", '
        '"attendees": ["my team"], "duration_minutes": 45, '
        '"timeframe": "next week", "confidence": 0.9}'
    )
    intent = parse_detection_response(raw)
    assert intent.is_scheduling is True
    assert intent.title == "Writing Studio training"
    assert intent.attendees == ["my team"]
    assert intent.duration_minutes == 45
    assert intent.timeframe == "next week"
    assert intent.confidence == pytest.approx(0.9)


def test_parse_detection_with_code_fence_and_prose():
    raw = 'Here you go:\n```json\n{"is_scheduling": false, "confidence": 0.1}\n```'
    intent = parse_detection_response(raw)
    assert intent.is_scheduling is False


def test_parse_detection_garbage_fails_closed():
    assert parse_detection_response("not json at all").is_scheduling is False
    assert parse_detection_response("").is_scheduling is False
    assert parse_detection_response("[1,2,3]").is_scheduling is False


def test_parse_detection_bad_duration_defaults():
    intent = parse_detection_response(
        '{"is_scheduling": true, "duration_minutes": "oops", "confidence": 0.8}'
    )
    assert intent.duration_minutes == 60
    intent2 = parse_detection_response(
        '{"is_scheduling": true, "duration_minutes": -5, "confidence": 0.8}'
    )
    assert intent2.duration_minutes == 60


def test_build_detection_prompt_includes_inputs():
    prompt = build_detection_prompt(action_item_text="schedule training", meeting_title="Team sync")
    assert "schedule training" in prompt
    assert "Team sync" in prompt


# ── classify_action_item with a fake adapter ──────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str):
        class _Block:
            def __init__(self, t):
                self.text = t

        class _Msg:
            def __init__(self, t):
                self.content = [_Block(t)]

        self.message = _Msg(text)


class _FakeAdapter:
    def __init__(self, text: str):
        self._text = text
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        return _FakeResponse(self._text)


@pytest.mark.asyncio
async def test_classify_returns_scheduling_intent():
    adapter = _FakeAdapter(
        '{"is_scheduling": true, "title": "Training", "attendees": [], '
        '"duration_minutes": 60, "timeframe": "next week", "confidence": 0.85}'
    )
    intent = await classify_action_item(
        action_item_text="set up a training", meeting_title="Sync", adapter=adapter
    )
    assert intent.is_scheduling is True
    assert intent.title == "Training"
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_classify_low_confidence_treated_as_no():
    adapter = _FakeAdapter('{"is_scheduling": true, "title": "Maybe", "confidence": 0.3}')
    intent = await classify_action_item(
        action_item_text="ambiguous", meeting_title="Sync", adapter=adapter
    )
    assert intent.is_scheduling is False


@pytest.mark.asyncio
async def test_classify_adapter_failure_fails_closed():
    class _Boom:
        async def complete(self, request):
            raise RuntimeError("provider down")

    intent = await classify_action_item(action_item_text="x", meeting_title="y", adapter=_Boom())
    assert intent.is_scheduling is False


# ── Search window ─────────────────────────────────────────────────────────────


def test_search_window_next_week_starts_next_monday():
    # Wednesday 2026-06-17
    now = datetime(2026, 6, 17, 12, 0, tzinfo=TZ).astimezone(UTC)
    start, end = resolve_search_window("next week", now=now, tz=TZ)
    start_local = start.astimezone(TZ)
    assert start_local.weekday() == 0  # Monday
    assert start_local.date() == datetime(2026, 6, 22).date()
    assert end > start


def test_search_window_vague_falls_back_to_horizon():
    now = datetime(2026, 6, 17, 12, 0, tzinfo=TZ).astimezone(UTC)
    start, end = resolve_search_window("", now=now, tz=TZ)
    # Starts tomorrow, never today.
    assert start.astimezone(TZ).date() == datetime(2026, 6, 18).date()
    assert (end - start).days >= 9


def test_search_window_never_starts_today():
    now = datetime(2026, 6, 17, 9, 0, tzinfo=TZ).astimezone(UTC)
    start, _ = resolve_search_window("tomorrow", now=now, tz=TZ)
    assert start.astimezone(TZ).date() > now.astimezone(TZ).date()


# ── Free slot finding ─────────────────────────────────────────────────────────


def test_find_free_slots_empty_calendar():
    # Thursday window
    ws = datetime(2026, 6, 18, 0, 0, tzinfo=TZ).astimezone(UTC)
    we = datetime(2026, 6, 18, 23, 59, tzinfo=TZ).astimezone(UTC)
    slots = find_free_slots(
        busy=[], window_start=ws, window_end=we, duration_minutes=60, tz=TZ, count=3
    )
    assert len(slots) == 3
    # First slot at 9:00 local.
    first_local = slots[0].start.astimezone(TZ)
    assert first_local.hour == 9
    # Slots don't overlap.
    assert slots[1].start >= slots[0].end


def test_find_free_slots_avoids_busy():
    ws = datetime(2026, 6, 18, 0, 0, tzinfo=TZ).astimezone(UTC)
    we = datetime(2026, 6, 18, 23, 59, tzinfo=TZ).astimezone(UTC)
    # Busy 9-12 local — first slot should be at/after noon.
    busy = [
        BusyInterval(
            start=datetime(2026, 6, 18, 9, 0, tzinfo=TZ).astimezone(UTC),
            end=datetime(2026, 6, 18, 12, 0, tzinfo=TZ).astimezone(UTC),
        )
    ]
    slots = find_free_slots(
        busy=busy, window_start=ws, window_end=we, duration_minutes=60, tz=TZ, count=1
    )
    assert len(slots) == 1
    assert slots[0].start.astimezone(TZ).hour >= 12


def test_find_free_slots_skips_weekend():
    # Saturday 2026-06-20 only.
    ws = datetime(2026, 6, 20, 0, 0, tzinfo=TZ).astimezone(UTC)
    we = datetime(2026, 6, 20, 23, 59, tzinfo=TZ).astimezone(UTC)
    slots = find_free_slots(
        busy=[], window_start=ws, window_end=we, duration_minutes=60, tz=TZ, count=3
    )
    assert slots == []


def test_find_free_slots_respects_working_hours():
    ws = datetime(2026, 6, 18, 0, 0, tzinfo=TZ).astimezone(UTC)
    we = datetime(2026, 6, 18, 23, 59, tzinfo=TZ).astimezone(UTC)
    slots = find_free_slots(
        busy=[], window_start=ws, window_end=we, duration_minutes=60, tz=TZ, count=10
    )
    for s in slots:
        local = s.start.astimezone(TZ)
        assert 9 <= local.hour < 17
        assert s.end.astimezone(TZ).hour <= 17


# ── Free/busy parsing (THE key coworker degradation behavior) ─────────────────


def test_parse_freebusy_busy_intervals():
    data = {
        "calendars": {
            "primary": {"busy": [{"start": "2026-06-18T15:00:00Z", "end": "2026-06-18T16:00:00Z"}]}
        }
    }
    busy, unreadable = parse_freebusy_response(data, requested_calendars=["primary"])
    assert len(busy) == 1
    assert unreadable == []
    assert busy[0].start == datetime(2026, 6, 18, 15, 0, tzinfo=UTC)


def test_parse_freebusy_coworker_unreadable_is_flagged_not_free():
    # KEY CASE: coworker calendar returns an errors array → must be flagged
    # as unreadable (pending), NEVER silently treated as free.
    data = {
        "calendars": {
            "primary": {"busy": []},
            "coworker@org.com": {"errors": [{"domain": "global", "reason": "notFound"}]},
        }
    }
    busy, unreadable = parse_freebusy_response(
        data, requested_calendars=["primary", "coworker@org.com"]
    )
    assert "coworker@org.com" in unreadable
    assert busy == []  # no busy data, but coworker NOT assumed free


def test_parse_freebusy_omitted_calendar_is_unreadable():
    data = {"calendars": {"primary": {"busy": []}}}
    busy, unreadable = parse_freebusy_response(
        data, requested_calendars=["primary", "missing@org.com"]
    )
    assert unreadable == ["missing@org.com"]


# ── Attendee split ────────────────────────────────────────────────────────────


def test_split_attendees_emails_vs_names():
    emails, labels = split_resolved_attendees(["alice@org.com", "my team", "bob@org.com", "Angela"])
    assert emails == ["alice@org.com", "bob@org.com"]
    assert labels == ["my team", "Angela"]


# ── Proposal rendering ────────────────────────────────────────────────────────


def _slot(h: int) -> CandidateSlot:
    return CandidateSlot(
        start=datetime(2026, 6, 23, h, 0, tzinfo=TZ).astimezone(UTC),
        end=datetime(2026, 6, 23, h + 1, 0, tzinfo=TZ).astimezone(UTC),
    )


def test_format_proposal_message_basic():
    proposal = SlotProposal(
        intent=SchedulingIntent(
            is_scheduling=True, title="Writing Studio training", confidence=0.9
        ),
        slots=[_slot(10), _slot(13)],
        resolved_attendees=["alice@org.com"],
        unresolved_attendees=[],
        availability_pending=[],
    )
    msg = format_proposal_message(proposal, tz=TZ)
    assert "Writing Studio training" in msg
    assert "alice@org.com" in msg
    assert "1." in msg and "2." in msg
    # Conversational confirmation, no buttons.
    assert "yes" in msg.lower()
    assert "button" not in msg.lower()


def test_format_proposal_message_flags_pending_coworkers():
    proposal = SlotProposal(
        intent=SchedulingIntent(is_scheduling=True, title="Sync", confidence=0.9),
        slots=[_slot(10)],
        resolved_attendees=["bob@org.com"],
        unresolved_attendees=["my team"],
        availability_pending=["bob@org.com"],
    )
    msg = format_proposal_message(proposal, tz=TZ)
    assert "pending" in msg.lower()
    assert "my team" in msg
    assert "bob@org.com" in msg


def test_format_proposal_message_empty_when_no_slots():
    proposal = SlotProposal(
        intent=SchedulingIntent(is_scheduling=True, title="X", confidence=0.9),
        slots=[],
        resolved_attendees=[],
        unresolved_attendees=[],
        availability_pending=[],
    )
    assert format_proposal_message(proposal, tz=TZ) == ""


def test_format_slot_label_human_readable():
    label = format_slot_label(_slot(10), tz=TZ)
    assert "10:00" in label
    assert ":00" in label


# ── calendar.create payload (agency-gate contract) ────────────────────────────


def test_build_calendar_create_payload_shape():
    payload = build_calendar_create_payload(
        slot=_slot(10),
        title="Training",
        attendees=["alice@org.com"],
        description="from meeting",
        tz_name=TZ_NAME,
    )
    # Matches what _execute_calendar_create consumes.
    assert payload["calendar_id"] == "primary"
    assert payload["summary"] == "Training"
    assert payload["attendees"] == ["alice@org.com"]
    assert payload["description"] == "from meeting"
    assert "dateTime" in payload["start"] and "timeZone" in payload["start"]
    assert "dateTime" in payload["end"] and "timeZone" in payload["end"]
    assert payload["start"]["timeZone"] == TZ_NAME


def test_build_proposal_preview_includes_title_and_first_slot():
    proposal = SlotProposal(
        intent=SchedulingIntent(is_scheduling=True, title="Training", confidence=0.9),
        slots=[_slot(10)],
        resolved_attendees=["alice@org.com"],
        unresolved_attendees=[],
        availability_pending=[],
    )
    preview = build_proposal_preview(proposal, tz=TZ)
    assert "Training" in preview
    assert "alice@org.com" in preview


# ── Dedup key ─────────────────────────────────────────────────────────────────


def test_scheduled_dedup_key_stable_and_namespaced():
    k = scheduled_dedup_key("g123", "abc")
    assert k.startswith("post_meeting_schedule_proposed:")
    assert "g123" in k and "abc" in k
    assert scheduled_dedup_key("g123", "abc") == k
