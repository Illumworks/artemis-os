"""Unit tests for the post-meeting scheduling owner-attribution gate.

Bug: Artemis was proposing calendar invites for OTHER people's action items
because run_post_meeting_scheduling_sweep read only ``item["text"]`` and
dropped ``item["owner"]`` entirely — there was no gate at all.

Fix: mirror commitments.ingest_meeting_commitments's opt-in gate — resolve
the item's owner label to a user id and only ever propose a calendar action
when it resolves to the canonical system owner (Jon). These tests mock the
DB session / all I/O boundaries (LLM classify, GCal, Slack, agency gate) so
they exercise the owner-gating control flow in isolation, without requiring
a live database.

Covers:
1. owner resolves to Jon -> eligible -> propose_action + DM fire, no skip.
2. owner resolves to someone else -> skipped -> propose_action never called,
   skipped_not_owner counted, item still marked proposed (no re-classify spam).
3. owner unstated/unresolvable -> fails closed -> also skipped.
"""

from __future__ import annotations

from contextlib import ExitStack
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.meetings.models import MeetingSummary
from artemis.proactivity.post_meeting_scheduling import (
    CandidateSlot,
    SchedulingIntent,
    SlotProposal,
    run_post_meeting_scheduling_sweep,
)

_NOW = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)
_OWNER_ID = 42
_OTHER_ID = 99


def _meeting(
    *, granola_id: str, owner: str | None, text: str = "Schedule a training"
) -> MeetingSummary:
    return MeetingSummary(
        granola_id=granola_id,
        title="Weekly sync",
        summary="",
        action_items=[{"text": text, "owner": owner, "due": None}],
        created_at=_NOW,
    )


def _make_session(meetings: list[MeetingSummary]) -> AsyncMock:
    """Mock AsyncSession whose one execute() call returns the given meetings."""
    session = AsyncMock()

    result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all = MagicMock(return_value=meetings)
    result.scalars = MagicMock(return_value=scalars_result)

    async def _execute(*_a: object, **_kw: object) -> MagicMock:
        return result

    session.execute = _execute
    session.commit = AsyncMock()
    return session


def _scheduling_intent() -> SchedulingIntent:
    return SchedulingIntent(
        is_scheduling=True,
        title="Writing Studio training",
        attendees=[],
        confidence=0.9,
        owner_is_operator=True,
    )


def _slot_proposal(intent: SchedulingIntent) -> SlotProposal:
    return SlotProposal(
        intent=intent,
        slots=[CandidateSlot(start=_NOW, end=_NOW)],
        resolved_attendees=[],
        unresolved_attendees=[],
        availability_pending=[],
    )


def _patched_sweep(
    *,
    canonical_owner_id: int | None,
    item_owner_id: int | None,
    intent: SchedulingIntent,
):
    """Context manager stack patching every I/O boundary of the sweep.

    Returns the tuple of mocks the caller wants to assert against:
    (mock_propose_action, mock_send_dm, mock_mark_proposed).
    """
    mock_propose_action = AsyncMock(return_value=MagicMock(id=55))
    mock_send_dm = AsyncMock()
    mock_mark_proposed = AsyncMock()

    patches = [
        patch(
            "artemis.proactivity.commitments._resolve_canonical_owner_user_id",
            new=AsyncMock(return_value=canonical_owner_id),
        ),
        patch(
            "artemis.proactivity.commitments._resolve_owner_user_id",
            new=AsyncMock(return_value=item_owner_id),
        ),
        patch(
            "artemis.proactivity.commitments._resolve_artemis_dm_recipient",
            new=AsyncMock(return_value="U12345"),
        ),
        patch(
            "artemis.proactivity.post_meeting_scheduling._resolve_gcal_client_io",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.proactivity.post_meeting_scheduling._already_proposed",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "artemis.proactivity.post_meeting_scheduling._mark_proposed",
            new=mock_mark_proposed,
        ),
        patch(
            "artemis.proactivity.post_meeting_scheduling.classify_action_item",
            new=AsyncMock(return_value=intent),
        ),
        patch(
            "artemis.proactivity.post_meeting_scheduling.build_slot_proposal",
            new=AsyncMock(return_value=_slot_proposal(intent)),
        ),
        patch(
            "artemis.proactivity.agency_gate.propose_action",
            new=mock_propose_action,
        ),
        patch(
            "artemis.proactivity.post_meeting_scheduling._send_scheduling_dm",
            new=mock_send_dm,
        ),
    ]
    return patches, mock_propose_action, mock_send_dm, mock_mark_proposed


@pytest.mark.asyncio
async def test_owner_is_jon_eligible_for_calendar_proposal() -> None:
    """owner resolves to the canonical owner (Jon) -> calendar proposal IS sent."""
    meeting = _meeting(granola_id="g-jon", owner="Jon")
    session = _make_session([meeting])
    intent = _scheduling_intent()

    patches, mock_propose_action, mock_send_dm, mock_mark_proposed = _patched_sweep(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=_OWNER_ID,
        intent=intent,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        summary = await run_post_meeting_scheduling_sweep(session, adapter=MagicMock())

    assert summary.scheduling_items == 1
    assert summary.skipped_not_owner == 0
    assert summary.proposals_sent == 1
    mock_propose_action.assert_awaited_once()
    mock_send_dm.assert_awaited_once()
    mock_mark_proposed.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_is_someone_else_skipped_no_calendar_proposal() -> None:
    """owner resolves to a DIFFERENT user -> NO calendar proposal is ever sent."""
    meeting = _meeting(granola_id="g-alice", owner="Alice")
    session = _make_session([meeting])
    intent = _scheduling_intent()

    patches, mock_propose_action, mock_send_dm, mock_mark_proposed = _patched_sweep(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=_OTHER_ID,
        intent=intent,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        summary = await run_post_meeting_scheduling_sweep(session, adapter=MagicMock())

    assert summary.scheduling_items == 1
    assert summary.skipped_not_owner == 1
    assert summary.proposals_sent == 0
    mock_propose_action.assert_not_awaited()
    mock_send_dm.assert_not_awaited()
    # Still marked proposed so the sweep doesn't re-classify this item forever.
    mock_mark_proposed.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_unstated_fails_closed_no_calendar_proposal() -> None:
    """owner missing/unresolvable -> fail closed, no calendar proposal."""
    meeting = _meeting(granola_id="g-unstated", owner=None)
    session = _make_session([meeting])
    intent = _scheduling_intent()

    patches, mock_propose_action, mock_send_dm, mock_mark_proposed = _patched_sweep(
        canonical_owner_id=_OWNER_ID,
        item_owner_id=None,
        intent=intent,
    )
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        summary = await run_post_meeting_scheduling_sweep(session, adapter=MagicMock())

    assert summary.skipped_not_owner == 1
    assert summary.proposals_sent == 0
    mock_propose_action.assert_not_awaited()
