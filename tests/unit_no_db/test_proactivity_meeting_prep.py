"""Unit tests for artemis/proactivity/meeting_prep.py.

All tests are pure-function unit tests — no DB, no Slack, no GCal.
The injected I/O helpers (fetch_today_upcoming_events, etc.) are tested
by mocking their return values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from artemis.proactivity.meeting_prep import (
    MeetingPrepContext,
    UpcomingEvent,
    filter_events_needing_prep,
    format_prep_message,
    parse_already_sent_event_ids,
    prep_dedup_key,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _event(
    event_id: str = "evt1",
    title: str = "Sync with Alice",
    minutes_from_now: float = 20.0,
    attendees: list[str] | None = None,
) -> UpcomingEvent:
    now = datetime.now(UTC)
    start = now + timedelta(minutes=minutes_from_now)
    return UpcomingEvent(
        event_id=event_id,
        title=title,
        start_utc=start,
        end_utc=start + timedelta(minutes=30),
        attendees=attendees or ["alice@example.com"],
    )


# ── filter_events_needing_prep ────────────────────────────────────────────────


def test_filter_includes_event_in_window() -> None:
    now = datetime.now(UTC)
    evt = _event(minutes_from_now=20)
    result = filter_events_needing_prep([evt], now=now, already_sent=set())
    assert len(result) == 1
    assert result[0].event_id == "evt1"


def test_filter_excludes_too_far_ahead() -> None:
    now = datetime.now(UTC)
    evt = _event(minutes_from_now=60)  # 60 min away, lookahead=30
    result = filter_events_needing_prep([evt], now=now, already_sent=set())
    assert result == []


def test_filter_excludes_too_soon() -> None:
    now = datetime.now(UTC)
    evt = _event(minutes_from_now=3)  # 3 min away, too_late=5
    result = filter_events_needing_prep([evt], now=now, already_sent=set())
    assert result == []


def test_filter_excludes_already_sent() -> None:
    now = datetime.now(UTC)
    evt = _event(event_id="evt42", minutes_from_now=20)
    result = filter_events_needing_prep([evt], now=now, already_sent={"evt42"})
    assert result == []


def test_filter_multiple_events_picks_due_ones() -> None:
    now = datetime.now(UTC)
    events = [
        _event(event_id="e1", minutes_from_now=10),  # in window
        _event(event_id="e2", minutes_from_now=45),  # too far
        _event(event_id="e3", minutes_from_now=25),  # in window
        _event(event_id="e4", minutes_from_now=2),  # too soon
    ]
    result = filter_events_needing_prep(events, now=now, already_sent=set())
    ids = {e.event_id for e in result}
    assert ids == {"e1", "e3"}


def test_filter_custom_lookahead() -> None:
    now = datetime.now(UTC)
    evt = _event(minutes_from_now=45)
    result = filter_events_needing_prep([evt], now=now, already_sent=set(), lookahead_minutes=60)
    assert len(result) == 1


# ── format_prep_message ───────────────────────────────────────────────────────


def test_format_prep_message_basic() -> None:
    now = datetime.now(UTC)
    evt = _event(title="Strategy Review", minutes_from_now=25, attendees=["bob@example.com"])
    ctx = MeetingPrepContext(event=evt)
    msg = format_prep_message(ctx, now=now)
    assert "Strategy Review" in msg
    assert "25" in msg or "24" in msg  # timing rounding
    assert "bob@example.com" in msg


def test_format_prep_message_with_commitments() -> None:
    now = datetime.now(UTC)
    evt = _event(title="Sync")
    ctx = MeetingPrepContext(
        event=evt,
        related_commitments=[
            {"text": "Follow up on proposal", "due": "2026-06-20"},
            {"text": "Send deck slides"},
        ],
    )
    msg = format_prep_message(ctx, now=now)
    assert "Follow up on proposal" in msg
    assert "2026-06-20" in msg
    assert "Send deck slides" in msg


def test_format_prep_message_with_memory_snippets() -> None:
    now = datetime.now(UTC)
    evt = _event(title="Budget call")
    ctx = MeetingPrepContext(
        event=evt,
        memory_snippets=["Budget approved Q3 last year", "CFO prefers summary slides"],
    )
    msg = format_prep_message(ctx, now=now)
    assert "Budget approved" in msg
    assert "CFO prefers" in msg


def test_format_prep_message_caps_attendees_at_6() -> None:
    now = datetime.now(UTC)
    attendees = [f"person{i}@example.com" for i in range(10)]
    evt = _event(attendees=attendees)
    ctx = MeetingPrepContext(event=evt)
    msg = format_prep_message(ctx, now=now)
    assert "(+4 more)" in msg


def test_format_prep_message_no_attendees() -> None:
    now = datetime.now(UTC)
    evt = UpcomingEvent(
        event_id="e1",
        title="Solo focus block",
        start_utc=datetime.now(UTC) + timedelta(minutes=15),
        end_utc=None,
        attendees=[],
    )
    ctx = MeetingPrepContext(event=evt)
    msg = format_prep_message(ctx, now=now)
    assert "Solo focus block" in msg
    assert "With:" not in msg


# ── prep_dedup_key / parse_already_sent_event_ids ─────────────────────────────


def test_prep_dedup_key_format() -> None:
    key = prep_dedup_key("evt_abc123")
    assert key == "pre_meeting_prep_sent:evt_abc123"


def test_parse_already_sent_empty() -> None:
    result = parse_already_sent_event_ids([])
    assert result == set()


def test_parse_already_sent_from_pydantic_style_obs() -> None:
    obs = MagicMock()
    obs.content = "pre_meeting_prep_sent:evt_xyz"
    result = parse_already_sent_event_ids([obs])
    assert result == {"evt_xyz"}


def test_parse_already_sent_from_dict_obs() -> None:
    obs = {"content": "pre_meeting_prep_sent:evt_abc"}
    result = parse_already_sent_event_ids([obs])
    assert result == {"evt_abc"}


def test_parse_already_sent_ignores_unrelated_obs() -> None:
    observations = [
        {"content": "brief_exclusion:MT-123"},
        {"content": "pre_meeting_prep_sent:evt1"},
        {"content": "some other observation"},
        {"content": "pre_meeting_prep_sent:evt2"},
    ]
    result = parse_already_sent_event_ids(observations)
    assert result == {"evt1", "evt2"}


def test_parse_already_sent_ignores_empty_event_id() -> None:
    obs = {"content": "pre_meeting_prep_sent:"}
    result = parse_already_sent_event_ids([obs])
    assert result == set()


# ── Integration: filter then format (smoke) ───────────────────────────────────


def test_filter_then_format_smoke() -> None:
    """Ensure filter + format pipeline works end-to-end for a simple case."""
    now = datetime.now(UTC)
    events = [_event(event_id="e1", title="QBR Prep", minutes_from_now=15)]
    due = filter_events_needing_prep(events, now=now, already_sent=set())
    assert len(due) == 1

    ctx = MeetingPrepContext(event=due[0])
    msg = format_prep_message(ctx, now=now)
    assert "QBR Prep" in msg
    assert "*Coming up in" in msg
