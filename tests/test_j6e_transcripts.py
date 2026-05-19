"""Tests for J6e — persisted transcripts + double-column Past tab.

Covers:
  - GET /api/meetings/{id}/summary response includes transcript field
  - Lazy backfill: transcript IS NULL → fetches from Granola + persists
  - Lazy backfill idempotency: existing transcript is NOT overwritten
  - Granola unavailable during backfill: returns summary without transcript (no crash)
  - Summarizer _process_event writes transcript alongside summary
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── GET /api/meetings/{granola_id}/summary — transcript field ─────────────────


@pytest.mark.asyncio
async def test_summary_route_includes_transcript_field() -> None:
    """Response always includes transcript key (may be None)."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-tx1",
        gcal_event_id=None,
        title="Transcript Test",
        summary="- Key point",
        action_items=[],
        transcript="Full verbatim transcript text here.",
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 10

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_summary
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with patch(
        "artemis.routes.meetings._get_granola_client", new_callable=AsyncMock, return_value=None
    ):
        response = await get_meeting_summary(granola_id="g-tx1", session=mock_session)

    assert "transcript" in response
    assert response["transcript"] == "Full verbatim transcript text here."


@pytest.mark.asyncio
async def test_summary_route_transcript_none_when_absent() -> None:
    """transcript field is None when not yet populated and Granola unavailable."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-tx2",
        gcal_event_id=None,
        title="No Transcript Yet",
        summary="- Summary only",
        action_items=[],
        transcript=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 11

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = fake_summary
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Granola not connected → backfill skipped.
    with patch(
        "artemis.routes.meetings._get_granola_client", new_callable=AsyncMock, return_value=None
    ):
        response = await get_meeting_summary(granola_id="g-tx2", session=mock_session)

    assert response["transcript"] is None


# ── Lazy backfill: transcript IS NULL → fetch + persist ──────────────────────


@pytest.mark.asyncio
async def test_summary_route_lazy_backfill_populates_transcript() -> None:
    """When transcript IS NULL and Granola is available, backfill fetches and persists."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-backfill",
        gcal_event_id=None,
        title="Backfill Me",
        summary="- Summary",
        action_items=[],
        transcript=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 12

    # execute() is called twice: once for SELECT (summary lookup) + once for UPDATE.
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = fake_summary
    update_result = MagicMock()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[select_result, update_result])
    mock_session.commit = AsyncMock()

    mock_granola = AsyncMock()
    mock_granola.get_meeting = AsyncMock(return_value={"transcript": "Verbatim meeting text."})

    with patch(
        "artemis.routes.meetings._get_granola_client",
        new_callable=AsyncMock,
        return_value=mock_granola,
    ):
        response = await get_meeting_summary(granola_id="g-backfill", session=mock_session)

    # Session should have committed the backfill.
    mock_session.commit.assert_called_once()
    # Response carries the fetched transcript.
    assert response["transcript"] == "Verbatim meeting text."


@pytest.mark.asyncio
async def test_summary_route_lazy_backfill_uses_notes_fallback() -> None:
    """Backfill uses 'notes' key when 'transcript' key is absent from Granola payload."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-notes",
        gcal_event_id=None,
        title="Notes Meeting",
        summary="- Notes summary",
        action_items=[],
        transcript=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 13

    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = fake_summary
    update_result = MagicMock()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[select_result, update_result])
    mock_session.commit = AsyncMock()

    mock_granola = AsyncMock()
    mock_granola.get_meeting = AsyncMock(return_value={"notes": "Meeting notes content."})

    with patch(
        "artemis.routes.meetings._get_granola_client",
        new_callable=AsyncMock,
        return_value=mock_granola,
    ):
        response = await get_meeting_summary(granola_id="g-notes", session=mock_session)

    assert response["transcript"] == "Meeting notes content."


@pytest.mark.asyncio
async def test_summary_route_lazy_backfill_idempotent() -> None:
    """When transcript already exists, backfill is skipped (no extra DB write)."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-already",
        gcal_event_id=None,
        title="Already Backfilled",
        summary="- Summary",
        action_items=[],
        transcript="Existing transcript — must not be overwritten.",
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 14

    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = fake_summary
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=select_result)
    mock_session.commit = AsyncMock()

    mock_granola = AsyncMock()

    with patch(
        "artemis.routes.meetings._get_granola_client",
        new_callable=AsyncMock,
        return_value=mock_granola,
    ):
        response = await get_meeting_summary(granola_id="g-already", session=mock_session)

    # Granola.get_meeting was never called (transcript already present).
    mock_granola.get_meeting.assert_not_called()
    # No commit (no UPDATE issued).
    mock_session.commit.assert_not_called()
    assert response["transcript"] == "Existing transcript — must not be overwritten."


@pytest.mark.asyncio
async def test_summary_route_backfill_granola_error_does_not_crash() -> None:
    """Granola error during backfill is swallowed; response still returns summary."""
    from artemis.meetings.models import MeetingSummary
    from artemis.routes.meetings import get_meeting_summary

    fake_summary = MeetingSummary(
        granola_id="g-err",
        gcal_event_id=None,
        title="Error During Backfill",
        summary="- Summary",
        action_items=[],
        transcript=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    fake_summary.id = 15

    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = fake_summary
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=select_result)
    mock_session.commit = AsyncMock()

    mock_granola = AsyncMock()
    mock_granola.get_meeting = AsyncMock(side_effect=Exception("Granola network error"))

    with patch(
        "artemis.routes.meetings._get_granola_client",
        new_callable=AsyncMock,
        return_value=mock_granola,
    ):
        response = await get_meeting_summary(granola_id="g-err", session=mock_session)

    # Response must still be returned (no exception propagated).
    assert response["granola_id"] == "g-err"
    assert response["transcript"] is None
    # Commit was not called because backfill failed.
    mock_session.commit.assert_not_called()


# ── Summarizer writes transcript ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_process_event_writes_transcript_to_db() -> None:
    """_process_event passes transcript text to the INSERT statement."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import GranolaClient, Meeting
    from artemis.meetings.summarizer import _process_event
    from artemis.memory.raw_inputs import RawInput

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)

    event = Event(
        id="evt-tx",
        summary="Transcript Write Test",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )
    granola_meeting = Meeting(
        id="g-tx-write",
        title="Transcript Write Test",
        date_raw=ended.isoformat(),
        date_ms=int(ended.timestamp() * 1000),
        participants=[],
    )

    mock_db_result_no_existing = MagicMock()
    mock_db_result_no_existing.scalar_one_or_none.return_value = None
    mock_execute_result = MagicMock()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=[mock_db_result_no_existing, mock_execute_result])
    mock_session.commit = AsyncMock()
    mock_session.add = lambda row: None

    mock_nested = AsyncMock()
    mock_nested.__aenter__ = AsyncMock(return_value=mock_nested)
    mock_nested.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin_nested = MagicMock(return_value=mock_nested)

    fake_raw = RawInput(
        created_at=now,
        source_kind="meeting_summary",
        source_id="g-tx-write",
        actor="artemis-scheduler",
        scope_kind="user",
        scope_id="jon",
        payload={},
        payload_hash="ph",
        prev_hash=None,
        this_hash="th",
    )
    fake_raw.id = 99

    mock_granola = MagicMock(spec=GranolaClient)
    mock_granola.get_meeting = AsyncMock(
        return_value={"transcript": "Verbatim transcript content for this meeting."}
    )

    mock_session.execute = AsyncMock(
        side_effect=[mock_db_result_no_existing, *([mock_execute_result] * 5)]
    )

    with (
        patch(
            "artemis.meetings.summarizer.insert_raw_input",
            new_callable=AsyncMock,
            return_value=fake_raw,
        ),
        patch(
            "artemis.meetings.summarizer._llm_summarize",
            new_callable=AsyncMock,
            return_value=("- Bullet point", []),
        ),
        patch("artemis.meetings.summarizer.pg_insert") as mock_pg_insert,
    ):
        # Capture .values() call to verify transcript is included.
        mock_table_insert = MagicMock()
        mock_values_result = MagicMock()
        mock_conflict_result = MagicMock()
        mock_pg_insert.return_value = mock_table_insert
        mock_table_insert.values = MagicMock(return_value=mock_values_result)
        mock_values_result.on_conflict_do_nothing = MagicMock(return_value=mock_conflict_result)

        await _process_event(mock_session, event, mock_granola, [granola_meeting])

    # Verify transcript was included in the INSERT .values() call.
    mock_table_insert.values.assert_called_once()
    values_kwargs = mock_table_insert.values.call_args.kwargs
    assert "transcript" in values_kwargs
    assert values_kwargs["transcript"] == "Verbatim transcript content for this meeting."


@pytest.mark.asyncio
async def test_process_event_transcript_none_when_empty_payload() -> None:
    """transcript is None in INSERT when Granola returns no transcript or notes."""
    from artemis.integrations.gcal.types import Event, EventDateTime
    from artemis.integrations.granola.client import GranolaClient, Meeting
    from artemis.meetings.summarizer import _process_event
    from artemis.memory.raw_inputs import RawInput

    now = datetime.now(UTC)
    ended = now - timedelta(minutes=5)

    event = Event(
        id="evt-empty",
        summary="Empty Payload Meeting",
        start=EventDateTime(dateTime=(ended - timedelta(hours=1)).isoformat()),
        end=EventDateTime(dateTime=ended.isoformat()),
    )
    granola_meeting = Meeting(
        id="g-empty",
        title="Empty Payload Meeting",
        date_raw=ended.isoformat(),
        date_ms=int(ended.timestamp() * 1000),
        participants=[],
    )

    mock_db_no_existing = MagicMock()
    mock_db_no_existing.scalar_one_or_none.return_value = None
    mock_execute_result = MagicMock()

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(
        side_effect=[mock_db_no_existing, *([mock_execute_result] * 5)]
    )
    mock_session.add = lambda row: None

    mock_nested = AsyncMock()
    mock_nested.__aenter__ = AsyncMock(return_value=mock_nested)
    mock_nested.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin_nested = MagicMock(return_value=mock_nested)

    fake_raw = RawInput(
        created_at=now,
        source_kind="meeting_summary",
        source_id="g-empty",
        actor="artemis-scheduler",
        scope_kind="user",
        scope_id="jon",
        payload={},
        payload_hash="ph2",
        prev_hash=None,
        this_hash="th2",
    )
    fake_raw.id = 100

    mock_granola = MagicMock(spec=GranolaClient)
    # Payload has neither 'transcript' nor 'notes' — only other keys.
    mock_granola.get_meeting = AsyncMock(return_value={"title": "Empty Payload Meeting"})

    with (
        patch(
            "artemis.meetings.summarizer.insert_raw_input",
            new_callable=AsyncMock,
            return_value=fake_raw,
        ),
        patch(
            "artemis.meetings.summarizer._llm_summarize",
            new_callable=AsyncMock,
            return_value=("- Summary", []),
        ),
        patch("artemis.meetings.summarizer.pg_insert") as mock_pg_insert,
    ):
        mock_table_insert = MagicMock()
        mock_values_result = MagicMock()
        mock_conflict_result = MagicMock()
        mock_pg_insert.return_value = mock_table_insert
        mock_table_insert.values = MagicMock(return_value=mock_values_result)
        mock_values_result.on_conflict_do_nothing = MagicMock(return_value=mock_conflict_result)

        await _process_event(mock_session, event, mock_granola, [granola_meeting])

    values_kwargs = mock_table_insert.values.call_args.kwargs
    assert values_kwargs["transcript"] is None
