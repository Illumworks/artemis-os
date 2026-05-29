"""Tests for J6d — calendar-driven post-meeting auto-summary.

Covers:
  - find_recently_ended_meetings: returns events in window, skips already-summarized
  - find_granola_match: exact, fuzzy, miss (3 cases)
  - _title_match_score: unit-level match scoring
  - run_summarizer_tick: scheduler tick, idempotency
  - M1 raw_input written on summarization
  - GET /api/meetings/{granola_id}/summary: 200 + 404
  - Floating Artemis _build_system_prompt: recent meeting context injection
  - scheduler start/stop lifecycle
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── _title_match_score ────────────────────────────────────────────────────────


def test_title_match_exact_case_insensitive() -> None:
    from artemis.meetings.summarizer import _title_match_score

    kind, score = _title_match_score("Team Sync", "team sync")
    assert kind == "exact"
    assert score == 1.0


def test_title_match_exact_identical() -> None:
    from artemis.meetings.summarizer import _title_match_score

    kind, score = _title_match_score("Q2 Planning", "Q2 Planning")
    assert kind == "exact"
    assert score == 1.0


def test_title_match_fuzzy_substring() -> None:
    from artemis.meetings.summarizer import _title_match_score

    # GCal title is shorter; Granola title is longer
    kind, score = _title_match_score("Standup", "Amira Standup — Daily Check-in")
    assert kind == "fuzzy"
    assert score == 0.5


def test_title_match_fuzzy_reverse_substring() -> None:
    from artemis.meetings.summarizer import _title_match_score

    # Granola title is shorter and contained in GCal title
    kind, score = _title_match_score("Amira Daily Standup", "standup")
    assert kind == "fuzzy"
    assert score == 0.5


def test_title_match_no_match() -> None:
    from artemis.meetings.summarizer import _title_match_score

    kind, score = _title_match_score("Board Meeting", "Investor Call")
    assert kind == "none"
    assert score == 0.0


# ── find_granola_match — three cases ─────────────────────────────────────────


def _make_event(
    title: str = "Test Meeting",
    end_dt: datetime | None = None,
    event_id: str = "gcal-evt-1",
) -> Any:
    from artemis.integrations.gcal.types import Event, EventDateTime

    if end_dt is None:
        end_dt = datetime.now(UTC) - timedelta(minutes=5)
    return Event(
        id=event_id,
        summary=title,
        start=EventDateTime(dateTime=(end_dt - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=end_dt.isoformat()),
    )


def _make_granola_meeting(
    meeting_id: str,
    title: str,
    offset_hours: float = 0.0,
) -> Any:
    from artemis.integrations.granola.client import Meeting

    end_dt = datetime.now(UTC) - timedelta(minutes=5) - timedelta(hours=offset_hours)
    date_ms = int(end_dt.timestamp() * 1000)
    return Meeting(
        id=meeting_id,
        title=title,
        date_raw=end_dt.isoformat(),
        date_ms=date_ms,
        participants=[],
    )


@pytest.mark.asyncio
async def test_find_granola_match_exact() -> None:
    """Exact title match returns the matching Granola meeting ID."""
    from artemis.meetings.summarizer import find_granola_match

    event = _make_event(title="Q2 Planning Session")
    granola_meetings = [
        _make_granola_meeting("gid-1", "Irrelevant Meeting"),
        _make_granola_meeting("gid-2", "Q2 Planning Session"),
    ]

    granola_id, match_kind, bc_id, bc_title = await find_granola_match(event, granola_meetings)

    assert granola_id == "gid-2"
    assert match_kind == "exact"
    assert bc_id == "gid-2"


@pytest.mark.asyncio
async def test_find_granola_match_fuzzy() -> None:
    """Fuzzy (substring) match when no exact match exists."""
    from artemis.meetings.summarizer import find_granola_match

    event = _make_event(title="Standup")
    granola_meetings = [
        _make_granola_meeting("gid-10", "Amira Daily Standup — All Hands"),
        _make_granola_meeting("gid-11", "Budget Review"),
    ]

    granola_id, match_kind, bc_id, bc_title = await find_granola_match(event, granola_meetings)

    assert granola_id == "gid-10"
    assert match_kind == "fuzzy"


@pytest.mark.asyncio
async def test_find_granola_match_miss_logs_best_candidate() -> None:
    """No match returns None granola_id but populates best_candidate fields."""
    from artemis.meetings.summarizer import find_granola_match

    event = _make_event(title="Board Meeting")
    granola_meetings = [
        _make_granola_meeting("gid-20", "Investor Call"),
        _make_granola_meeting("gid-21", "HR Review"),
    ]

    granola_id, match_kind, bc_id, bc_title = await find_granola_match(event, granola_meetings)

    assert granola_id is None
    assert match_kind is None
    # Best candidate is still returned for logging purposes
    assert bc_id is not None
    assert bc_title is not None


@pytest.mark.asyncio
async def test_find_granola_match_empty_list() -> None:
    """No Granola meetings means no match and None best candidate."""
    from artemis.meetings.summarizer import find_granola_match

    event = _make_event(title="Anything")
    granola_id, match_kind, bc_id, bc_title = await find_granola_match(event, [])

    assert granola_id is None
    assert bc_id is None
    assert bc_title is None


# ── find_recently_ended_meetings ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_recently_ended_gcal_not_connected() -> None:
    """Returns empty list when GCal is not connected."""
    from artemis.meetings.summarizer import find_recently_ended_meetings

    mock_session = AsyncMock()

    with patch(
        "artemis.integrations.repository.list_active", new_callable=AsyncMock, return_value=[]
    ):
        result = await find_recently_ended_meetings(mock_session)

    assert result == []


@pytest.mark.asyncio
async def test_find_recently_ended_skips_already_summarized() -> None:
    """Events already in meeting_summaries are excluded."""
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.meetings.summarizer import find_recently_ended_meetings

    creds = {
        "access_token": "tok",
        "refresh_token": "rt",
        "client_id": "cid",
        "client_secret": "csec",
    }
    encrypted = encrypt_credentials(creds)
    mock_row = MagicMock()
    mock_row.encrypted_credentials = encrypted

    now = datetime.now(UTC)
    ended_5m_ago = now - timedelta(minutes=5)

    event = Event(
        id="already-done",
        summary="Done Meeting",
        start=EventDateTime(dateTime=(ended_5m_ago - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended_5m_ago.isoformat()),
    )

    # Simulate DB query returning the event's ID as already-summarized.
    mock_db_result = MagicMock()
    mock_db_result.all.return_value = [("already-done",)]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    with (
        patch(
            "artemis.integrations.repository.list_active",
            new_callable=AsyncMock,
            return_value=[mock_row],
        ),
        patch(
            "artemis.integrations.gcal.client.GCalClient.list_events",
            new_callable=AsyncMock,
            return_value=[event],
        ),
    ):
        result = await find_recently_ended_meetings(mock_session)

    assert result == []


# ── Idempotency: run_summarizer_tick ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarizer_tick_no_events_is_noop() -> None:
    """Tick with no recently ended meetings does nothing (cheap idle)."""
    with (
        patch(
            "artemis.meetings.summarizer.find_recently_ended_meetings",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch("artemis.db.SessionLocal") as mock_sl,
    ):
        # Provide an async context manager for SessionLocal()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_sl.return_value = mock_session

        from artemis.meetings.summarizer import run_summarizer_tick

        await run_summarizer_tick()

    # Granola was never called (no events)
    # No exception raised — tick was a no-op


@pytest.mark.asyncio
async def test_summarizer_tick_skips_already_summarized_granola_id() -> None:
    """Second tick for same granola_id is a no-op (idempotency)."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import Meeting
    from artemis.meetings.models import MeetingSummary

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)

    event = Event(
        id="evt-idem",
        summary="Idempotency Test",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )
    granola_meeting = Meeting(
        id="g-idem",
        title="Idempotency Test",
        date_raw=ended.isoformat(),
        date_ms=int(ended.timestamp() * 1000),
        participants=[],
    )

    # Simulate existing MeetingSummary row for this granola_id.
    existing_summary = MagicMock(spec=MeetingSummary)
    existing_summary.granola_id = "g-idem"

    mock_db_result_no_gcal = MagicMock()
    mock_db_result_no_gcal.all.return_value = []  # no already-summarized gcal IDs
    mock_db_result_existing = MagicMock()
    mock_db_result_existing.scalar_one_or_none.return_value = existing_summary  # already exists

    call_count: list[int] = [0]

    async def _mock_execute(stmt: Any) -> Any:
        call_count[0] += 1
        if call_count[0] == 1:
            return mock_db_result_no_gcal  # first call: summarized gcal IDs
        return mock_db_result_existing  # second call: existing summary check

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=_mock_execute)
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    inserted_rows: list[Any] = []

    def _mock_add(row: Any) -> None:
        inserted_rows.append(row)

    mock_session.add = _mock_add

    with (
        patch(
            "artemis.meetings.summarizer.find_recently_ended_meetings",
            new_callable=AsyncMock,
            return_value=[event],
        ),
        patch(
            "artemis.meetings.summarizer._build_granola_client",
            new_callable=AsyncMock,
            return_value=MagicMock(
                list_meetings=AsyncMock(return_value=[granola_meeting]),
            ),
        ),
        patch("artemis.db.SessionLocal") as mock_sl,
    ):
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_sl.return_value = mock_session_ctx

        from artemis.meetings.summarizer import run_summarizer_tick

        await run_summarizer_tick()

    # No MeetingMatchLog row added for "summarized" (skipped) — only for new summaries.
    match_log_rows = [r for r in inserted_rows if hasattr(r, "outcome")]
    assert not any(r.outcome == "summarized" for r in match_log_rows)


# ── M1 raw_input written ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summarizer_writes_raw_input_and_summary() -> None:
    """Successful summarization writes both raw_input and meeting_summaries rows.

    Test calls _process_event directly to avoid the complexity of mocking the
    full session factory chain. Verifies insert_raw_input is called with the
    correct source/scope fields (lossless memory rule).
    """
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import GranolaClient, Meeting
    from artemis.memory.raw_inputs import RawInput

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)

    event = Event(
        id="evt-new",
        summary="New Meeting",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )
    granola_meeting = Meeting(
        id="g-new",
        title="New Meeting",
        date_raw=ended.isoformat(),
        date_ms=int(ended.timestamp() * 1000),
        participants=[],
    )

    # Simulate no existing summaries for idempotency check.
    mock_db_result_no_existing = MagicMock()
    mock_db_result_no_existing.scalar_one_or_none.return_value = None

    mock_execute_result = MagicMock()
    mock_execute_result.rowcount = 1

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[mock_db_result_no_existing, mock_execute_result])
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    added_rows: list[Any] = []
    mock_session.add = lambda row: added_rows.append(row)

    # begin_nested must return an async context manager.
    mock_nested = AsyncMock()
    mock_nested.__aenter__ = AsyncMock(return_value=mock_nested)
    mock_nested.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin_nested = MagicMock(return_value=mock_nested)

    fake_raw = RawInput(
        created_at=now,
        source_kind="meeting_summary",
        source_id="g-new",
        actor="artemis-scheduler",
        scope_kind="user",
        scope_id="jon",
        payload={},
        payload_hash="ph",
        prev_hash=None,
        this_hash="th",
    )
    fake_raw.id = 42

    mock_granola = MagicMock(spec=GranolaClient)
    mock_granola.get_meeting = AsyncMock(return_value={"transcript": "We discussed Q2 goals."})

    with (
        patch(
            "artemis.meetings.summarizer.insert_raw_input",
            new_callable=AsyncMock,
            return_value=fake_raw,
        ) as mock_raw_insert,
        patch(
            "artemis.meetings.summarizer._llm_summarize",
            new_callable=AsyncMock,
            return_value=("- Goal: ship J6d\n- Action: write tests", [{"text": "ship J6d"}]),
        ),
    ):
        from artemis.meetings.summarizer import _process_event

        await _process_event(mock_session, event, mock_granola, [granola_meeting])

    # insert_raw_input was called with correct source/scope (M1 lossless rule).
    mock_raw_insert.assert_called_once()
    call_kwargs = mock_raw_insert.call_args.kwargs
    assert call_kwargs["source_kind"] == "meeting_summary"
    assert call_kwargs["scope_kind"] == "user"
    assert call_kwargs["scope_id"] == "jon"
    assert call_kwargs["source_id"] == "g-new"


# ── GET /api/meetings/{granola_id}/summary ────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_route_404_when_missing() -> None:
    """GET /api/meetings/{id}/summary returns 404 when no summary exists."""
    from fastapi import HTTPException

    from artemis.routes.meetings import get_meeting_summary

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(HTTPException) as exc_info:
        await get_meeting_summary(granola_id="no-such-id", session=mock_session)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_summary_route_200_when_present() -> None:
    """GET /api/meetings/{id}/summary returns 200 with summary fields."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-123",
        gcal_event_id="gcal-evt-x",
        title="Q2 Planning",
        summary="- Goal: define OKRs\n- Action: ship by Friday",
        action_items=[{"text": "ship by Friday", "owner": "Jon", "due": None}],
        raw_input_id=7,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 1

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_summary
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    response = await get_meeting_summary(granola_id="g-123", session=mock_session)

    assert response["granola_id"] == "g-123"
    assert response["title"] == "Q2 Planning"
    assert "- Goal: define OKRs" in response["summary"]
    assert len(response["action_items"]) == 1
    assert response["raw_input_id"] == 7


# ── Floating Artemis prompt injection ─────────────────────────────────────────


def test_build_system_prompt_with_recent_meeting_context() -> None:
    """_build_system_prompt includes meeting context when provided."""
    from artemis.floating_artemis.chat import _build_system_prompt

    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        recent_meeting_context='You just finished "Q2 Planning". Summary: - Defined OKRs',
    )

    assert "Q2 Planning" in prompt
    assert "Defined OKRs" in prompt
    # H4: header reframed to mark summaries as LLM inferences with provenance.
    assert "Recent meeting summaries" in prompt
    assert "treat as inferences" in prompt


def test_build_system_prompt_without_recent_meeting_context() -> None:
    """_build_system_prompt omits meeting section when context is None."""
    from artemis.floating_artemis.chat import _build_system_prompt

    prompt = _build_system_prompt(
        voice_samples=[],
        page_context=None,
        available_surfaces=[],
        recent_meeting_context=None,
    )

    assert "Recent meeting summaries" not in prompt


@pytest.mark.asyncio
async def test_get_recent_meeting_context_no_db_rows() -> None:
    """_get_recent_meeting_context returns None when no recent summaries exist."""
    from artemis.floating_artemis.chat import _get_recent_meeting_context

    with patch(
        "artemis.meetings.summarizer.get_recent_summaries",
        new_callable=AsyncMock,
        return_value=[],
    ):
        mock_session = AsyncMock()
        result = await _get_recent_meeting_context(mock_session)

    assert result is None


@pytest.mark.asyncio
async def test_get_recent_meeting_context_formats_correctly() -> None:
    """_get_recent_meeting_context formats summary lines correctly."""
    from artemis.floating_artemis.chat import _get_recent_meeting_context
    from artemis.meetings.models import MeetingSummary

    fake_summary = MeetingSummary(
        granola_id="g-ctx",
        title="Product Sync",
        summary="- Reviewed roadmap\n- Agreed on timeline",
        action_items=[],
        gcal_event_id=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )

    with patch(
        "artemis.meetings.summarizer.get_recent_summaries",
        new_callable=AsyncMock,
        return_value=[fake_summary],
    ):
        mock_session = AsyncMock()
        result = await _get_recent_meeting_context(mock_session)

    assert result is not None
    assert "Product Sync" in result
    assert "You just finished" in result
    assert "Reviewed roadmap" in result


# ── Scheduler lifecycle ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_start_and_stop() -> None:
    """start_meeting_scheduler registers job; stop_meeting_scheduler shuts down."""
    import artemis.meetings.scheduler as sched_module
    from artemis.meetings.scheduler import (
        get_scheduler,
        start_meeting_scheduler,
        stop_meeting_scheduler,
    )

    # Reset global state before test.
    sched_module._scheduler = None

    start_meeting_scheduler()
    scheduler = get_scheduler()
    assert scheduler.running
    jobs = scheduler.get_jobs()
    assert any(j.id == "meeting_summarizer" for j in jobs)

    stop_meeting_scheduler()
    # After stop, scheduler is None (reset).
    assert sched_module._scheduler is None


@pytest.mark.asyncio
async def test_scheduler_idempotent_start() -> None:
    """Calling start_meeting_scheduler twice does not create duplicate jobs."""
    import artemis.meetings.scheduler as sched_module
    from artemis.meetings.scheduler import (
        get_scheduler,
        start_meeting_scheduler,
        stop_meeting_scheduler,
    )

    sched_module._scheduler = None

    start_meeting_scheduler()
    start_meeting_scheduler()  # second call; replace_existing=True

    jobs = get_scheduler().get_jobs()
    summarizer_jobs = [j for j in jobs if j.id == "meeting_summarizer"]
    assert len(summarizer_jobs) == 1

    stop_meeting_scheduler()


# ── _parse_event_end_dt edge cases ───────────────────────────────────────────


def test_parse_event_end_dt_none_when_no_datetime() -> None:
    """Returns None when event.end.date_time is absent (all-day events)."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.meetings.summarizer import _parse_event_end_dt

    event = Event(
        id="e1",
        summary="All Day Event",
        start=EventDateTime(date="2026-05-18"),
        end=EventDateTime(date="2026-05-18"),
    )
    result = _parse_event_end_dt(event)
    assert result is None


def test_parse_event_end_dt_naive_becomes_utc() -> None:
    """Naive datetime gets UTC tzinfo attached."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.meetings.summarizer import _parse_event_end_dt

    event = Event(
        id="e2",
        summary="Meeting",
        start=EventDateTime(dateTime="2026-05-18T09:00:00"),
        end=EventDateTime(dateTime="2026-05-18T10:00:00"),
    )
    result = _parse_event_end_dt(event)
    assert result is not None
    assert result.tzinfo is not None


def test_parse_event_end_dt_invalid_returns_none() -> None:
    """Invalid datetime string returns None."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.meetings.summarizer import _parse_event_end_dt

    event = Event(
        id="e3",
        summary="Meeting",
        start=EventDateTime(dateTime="not-a-date"),
        end=EventDateTime(dateTime="not-a-date"),
    )
    result = _parse_event_end_dt(event)
    assert result is None


# ── find_recently_ended_meetings — events returned ───────────────────────────


@pytest.mark.asyncio
async def test_find_recently_ended_returns_matching_events() -> None:
    """Returns events whose end_time falls within the window."""
    from artemis.integrations.crypto import encrypt_credentials
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.meetings.summarizer import find_recently_ended_meetings

    creds = {
        "access_token": "tok",
        "refresh_token": "rt",
        "client_id": "cid",
        "client_secret": "csec",
    }
    encrypted = encrypt_credentials(creds)
    mock_row = MagicMock()
    mock_row.encrypted_credentials = encrypted

    now = datetime.now(UTC)
    ended_10m_ago = now - timedelta(minutes=10)

    event = Event(
        id="in-window",
        summary="Recent Meeting",
        start=EventDateTime(dateTime=(ended_10m_ago - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended_10m_ago.isoformat()),
    )

    mock_db_result = MagicMock()
    mock_db_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result)

    with (
        patch(
            "artemis.integrations.repository.list_active",
            new_callable=AsyncMock,
            return_value=[mock_row],
        ),
        patch(
            "artemis.integrations.gcal.client.GCalClient.list_events",
            new_callable=AsyncMock,
            return_value=[event],
        ),
    ):
        result = await find_recently_ended_meetings(mock_session)

    assert len(result) == 1
    assert result[0].id == "in-window"


# ── find_granola_match — proximity tiebreak ──────────────────────────────────


@pytest.mark.asyncio
async def test_find_granola_match_proximity_tiebreak() -> None:
    """Date-proximity tiebreak selects the temporally closest fuzzy match."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import Meeting
    from artemis.meetings.summarizer import find_granola_match

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=10)

    close_ms = int((ended - timedelta(minutes=5)).timestamp() * 1000)
    far_ms = int((ended - timedelta(hours=3)).timestamp() * 1000)

    event = Event(
        id="prox-evt",
        summary="Standup",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )

    close_match = Meeting(
        id="close",
        title="Daily Standup",
        date_raw=ended.isoformat(),
        date_ms=close_ms,
        participants=[],
    )
    far_match = Meeting(
        id="far",
        title="Team Standup Review",
        date_raw=(ended - timedelta(hours=3)).isoformat(),
        date_ms=far_ms,
        participants=[],
    )

    granola_id, match_kind, bc_id, _ = await find_granola_match(event, [far_match, close_match])

    assert granola_id == "close"
    assert match_kind == "fuzzy"


# ── get_recent_summaries ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_recent_summaries_empty() -> None:
    """Returns empty list when no recent summaries exist."""
    from artemis.meetings.summarizer import get_recent_summaries

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await get_recent_summaries(mock_session, hours=4)
    assert result == []


@pytest.mark.asyncio
async def test_get_recent_summaries_returns_rows() -> None:
    """Returns summaries within the hours window."""
    from artemis.meetings.models import MeetingSummary
    from artemis.meetings.summarizer import get_recent_summaries

    fake = MeetingSummary(
        granola_id="g-recent",
        title="Recent Meeting",
        summary="- Key point",
        action_items=[],
        gcal_event_id=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fake]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await get_recent_summaries(mock_session, hours=4)
    assert len(result) == 1
    assert result[0].granola_id == "g-recent"


# ── _run_tick_in_session — Granola not connected ─────────────────────────────


@pytest.mark.asyncio
async def test_run_tick_granola_not_connected() -> None:
    """Tick with no Granola integration does not attempt to list Granola meetings."""
    from artemis.meetings.summarizer import _run_tick_in_session

    event = _make_event(title="Unmatched Meeting")
    mock_session = AsyncMock()

    with (
        patch(
            "artemis.meetings.summarizer.find_recently_ended_meetings",
            new_callable=AsyncMock,
            return_value=[event],
        ),
        patch(
            "artemis.meetings.summarizer._build_granola_client",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await _run_tick_in_session(mock_session)


@pytest.mark.asyncio
async def test_run_tick_granola_list_meetings_fails() -> None:
    """Tick gracefully handles Granola list_meetings failure."""
    from artemis.meetings.summarizer import _run_tick_in_session

    event = _make_event(title="Something")
    mock_session = AsyncMock()
    mock_granola = MagicMock()
    mock_granola.list_meetings = AsyncMock(side_effect=Exception("Granola error"))

    with (
        patch(
            "artemis.meetings.summarizer.find_recently_ended_meetings",
            new_callable=AsyncMock,
            return_value=[event],
        ),
        patch(
            "artemis.meetings.summarizer._build_granola_client",
            new_callable=AsyncMock,
            return_value=mock_granola,
        ),
    ):
        await _run_tick_in_session(mock_session)


# ── _process_event — no-transcript path ─────────────────────────────────────


@pytest.mark.asyncio
async def test_process_event_no_transcript_logs_and_returns() -> None:
    """When Granola returns empty transcript, log no_transcript and return."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import GranolaClient, Meeting
    from artemis.meetings.summarizer import _process_event

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)

    event = Event(
        id="evt-no-transcript",
        summary="No Transcript",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )
    granola_meeting = Meeting(
        id="g-no-t",
        title="No Transcript",
        date_raw=ended.isoformat(),
        date_ms=int(ended.timestamp() * 1000),
        participants=[],
    )

    mock_db_result_no_existing = MagicMock()
    mock_db_result_no_existing.scalar_one_or_none.return_value = None

    added_rows: list[Any] = []
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_db_result_no_existing)
    mock_session.add = lambda row: added_rows.append(row)
    mock_session.flush = AsyncMock()

    mock_granola = MagicMock(spec=GranolaClient)
    mock_granola.get_meeting = AsyncMock(return_value={})

    await _process_event(mock_session, event, mock_granola, [granola_meeting])

    match_log_rows = [r for r in added_rows if hasattr(r, "outcome")]
    assert any(r.outcome == "no_transcript" for r in match_log_rows)


@pytest.mark.asyncio
async def test_process_event_no_match_logs_and_returns() -> None:
    """When no Granola match found, logs no_match outcome."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import GranolaClient, Meeting
    from artemis.meetings.summarizer import _process_event

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)

    event = Event(
        id="evt-no-match",
        summary="Completely Unrelated Title",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )
    granola_meeting = Meeting(
        id="g-different",
        title="Different Meeting Name",
        date_raw=ended.isoformat(),
        date_ms=int(ended.timestamp() * 1000),
        participants=[],
    )

    added_rows: list[Any] = []
    mock_session = AsyncMock()
    mock_session.add = lambda row: added_rows.append(row)
    mock_session.flush = AsyncMock()

    mock_granola = MagicMock(spec=GranolaClient)

    await _process_event(mock_session, event, mock_granola, [granola_meeting])

    match_log_rows = [r for r in added_rows if hasattr(r, "outcome")]
    assert any(r.outcome == "no_match" for r in match_log_rows)


# ── _llm_summarize ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_summarize_happy_path() -> None:
    """_llm_summarize parses JSON response from LLM correctly."""
    import json

    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage
    from artemis.meetings.summarizer import _llm_summarize

    llm_json = json.dumps(
        {
            "bullets": ["Discussed roadmap", "Agreed on timeline", "Next steps defined"],
            # H4: "due" must be ISO 8601 or one of the allowed loose tokens —
            # "Friday" was previously accepted by the bare json.loads path but
            # now rejected by the Pydantic shape contract.
            "action_items": [{"text": "Ship J6d", "owner": "Jon", "due": "this week"}],
        }
    )
    good_response = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=llm_json)]),
        stop_reason="end_turn",
        usage=Usage(),
    )

    with (
        patch("artemis.providers.get_adapter", side_effect=Exception("no provider")),
        patch(
            "artemis.agent.client.AnthropicAdapter.complete",
            new_callable=AsyncMock,
            return_value=good_response,
        ),
    ):
        summary, actions = await _llm_summarize("Test Meeting", {"transcript": "content"})

    assert "- Discussed roadmap" in summary
    assert len(actions) == 1
    assert actions[0]["text"] == "Ship J6d"


@pytest.mark.asyncio
async def test_llm_summarize_fallback_on_bad_json() -> None:
    """_llm_summarize returns placeholder when LLM returns malformed JSON."""
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage
    from artemis.meetings.summarizer import _llm_summarize

    bad_response = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text="not valid json {{")]),
        stop_reason="end_turn",
        usage=Usage(),
    )

    with (
        patch("artemis.providers.get_adapter", side_effect=Exception("no provider")),
        patch(
            "artemis.agent.client.AnthropicAdapter.complete",
            new_callable=AsyncMock,
            return_value=bad_response,
        ),
    ):
        summary, actions = await _llm_summarize("Test", {"transcript": "content"})

    assert isinstance(summary, str)
    assert isinstance(actions, list)
