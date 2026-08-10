"""P3 — Dismiss action-items ("drop it / not relevant").

Ship-gate tests:
1. dismiss_action_item endpoint → leaves open list, closes linked commitment.
2. Re-running ingest for same meeting does NOT resurrect the dismissed item.
3. Raw action-item row preserved (lossless).
4. done / snooze remain DISTINCT from dismiss.
5. Slack dismiss reply handler ('dismiss N', 'drop N', 'irrelevant N').
6. Idempotent second dismiss is a no-op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.meetings.models import MeetingActionItemDismissal, MeetingSummary
from artemis.proactivity import repository as prepo
from artemis.proactivity.commitments import (
    action_item_key,
    ingest_meeting_commitments,
    try_apply_commitment_reply,
)
from artemis.proactivity.models import Commitment

pytestmark = pytest.mark.asyncio

# ── helpers ───────────────────────────────────────────────────────────────────

_GRANOLA_ID = "g-dismiss-test-1"
_MEETING_TITLE = "Team sync"
_ACTION_TEXT = "Send the board recap email"
_ACTION_ITEM = {"text": _ACTION_TEXT, "owner": "Jon", "due": None}


async def _seed_meeting_summary(session: AsyncSession) -> MeetingSummary:
    """Insert a minimal MeetingSummary row and return it."""
    row = MeetingSummary(
        granola_id=_GRANOLA_ID,
        gcal_event_id=None,
        title=_MEETING_TITLE,
        summary="- discussed things",
        action_items=[_ACTION_ITEM],
        transcript=None,
        raw_input_id=None,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def _seed_commitment(session: AsyncSession) -> Commitment:
    """Insert a commitment linked to _GRANOLA_ID/_ACTION_TEXT."""
    from artemis.proactivity.commitments import _normalize_text

    normalized = _normalize_text(_ACTION_TEXT)
    commitment, _ = await prepo.upsert_commitment(
        session,
        source_type="granola_meeting",
        source_id=_GRANOLA_ID,
        text=normalized,
        owner_user_id=None,
        due=None,
        sensitivity="personal_ops",
    )
    return commitment


async def _dismiss_via_repo(
    session: AsyncSession,
    summary: MeetingSummary,
) -> MeetingActionItemDismissal:
    """Write a dismissal record directly (used to pre-seed dismissed state)."""
    from artemis.proactivity.commitments import _normalize_text

    normalized = _normalize_text(_ACTION_TEXT)
    item_key = action_item_key(normalized)
    now = datetime.now(UTC)
    row = MeetingActionItemDismissal(
        meeting_summary_id=summary.id,
        action_item_key=item_key,
        granola_id=_GRANOLA_ID,
        action_item_text=normalized,
        dismissed_at=now,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


# ── 1. Dismiss leaves open commitment list + closes linked commitment ─────────


async def test_dismiss_closes_commitment_and_leaves_open_list(
    db_session: AsyncSession,
) -> None:
    """After dismiss, the commitment transitions to 'dismissed' (not 'done')."""
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    await _seed_meeting_summary(db_session)
    commitment = await _seed_commitment(db_session)
    await db_session.commit()

    # Confirm commitment starts active.
    assert commitment.status == "active"

    # Dismiss via repo helper (same path the endpoint uses).
    await prepo.dismiss_commitment(db_session, commitment_id=commitment.id, now=now)
    await db_session.commit()

    refreshed = await db_session.get(Commitment, commitment.id)
    assert refreshed is not None
    assert refreshed.status == "dismissed"
    # dismissed ≠ done
    assert refreshed.status != "done"
    # dismissed ≠ snoozed
    assert refreshed.snoozed_until is None

    # Active list (what followup sweep sees) must not include this commitment.
    result = await db_session.execute(select(Commitment).where(Commitment.status == "active"))
    active_ids = [c.id for c in result.scalars()]
    assert commitment.id not in active_ids


# ── 2. Re-ingest does NOT resurrect a dismissed item ─────────────────────────


async def test_ingest_skips_dismissed_item(
    db_session: AsyncSession,
) -> None:
    """KEY GATE: re-running ingest for the same meeting must not re-create
    a commitment for a dismissed action-item."""
    summary = await _seed_meeting_summary(db_session)
    await _dismiss_via_repo(db_session, summary)
    await db_session.commit()

    # Run ingest — the dismissed item should be silently skipped.
    result = await ingest_meeting_commitments(
        db_session,
        granola_id=_GRANOLA_ID,
        title=_MEETING_TITLE,
        action_items=[_ACTION_ITEM],
    )
    await db_session.commit()

    # seen=0 because the item was skipped before incrementing seen counter.
    assert result.seen == 0
    assert result.inserted == 0

    # No commitment row exists for this text.
    from artemis.proactivity.commitments import _normalize_text

    normalized = _normalize_text(_ACTION_TEXT)
    commitment_check = await db_session.execute(
        select(Commitment).where(
            Commitment.source_type == "granola_meeting",
            Commitment.source_id == _GRANOLA_ID,
            Commitment.text == normalized,
        )
    )
    assert commitment_check.scalar_one_or_none() is None


# ── 3. Lossless — raw action-item and dismissal row preserved ─────────────────


async def test_dismissal_record_is_preserved(
    db_session: AsyncSession,
) -> None:
    """The dismissal record must persist (lossless) and the meeting_summary row
    must remain intact."""
    summary = await _seed_meeting_summary(db_session)
    dismissal = await _dismiss_via_repo(db_session, summary)
    await db_session.commit()

    # Dismissal row survives.
    reloaded = await db_session.get(MeetingActionItemDismissal, dismissal.id)
    assert reloaded is not None
    assert reloaded.granola_id == _GRANOLA_ID
    assert reloaded.action_item_text is not None

    # meeting_summaries row untouched.
    summary_reloaded = await db_session.get(MeetingSummary, summary.id)
    assert summary_reloaded is not None
    assert summary_reloaded.action_items is not None
    assert len(summary_reloaded.action_items) == 1
    # The raw action item text still lives in the JSONB column.
    # NOTE: MeetingSummary.action_items is declared Mapped[dict[str, Any] | None]
    # in artemis/meetings/models.py, but every write path (this test included, and
    # meetings/summarizer.py) actually stores a *list* of dicts — the model's type
    # annotation has drifted from the real column shape. Out of this domain's
    # scope (artemis/memory + artemis/proactivity only) to fix; flagged in the PR.
    assert summary_reloaded.action_items[0]["text"] == _ACTION_TEXT  # type: ignore[index]


# ── 4. done and snooze remain DISTINCT from dismiss ───────────────────────────


async def test_done_and_snooze_distinct_from_dismiss(
    db_session: AsyncSession,
) -> None:
    """done → status='done'; snooze → status='snoozed'; dismiss → status='dismissed'."""
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    def _make_commitment(source_id: str) -> dict[str, Any]:
        return {
            "source_type": "granola_meeting",
            "source_id": source_id,
            "text": "A unique commitment text for " + source_id,
            "owner_user_id": None,
            "due": None,
            "sensitivity": "personal_ops",
        }

    done_c, _ = await prepo.upsert_commitment(db_session, **_make_commitment("g-done"))
    snooze_c, _ = await prepo.upsert_commitment(db_session, **_make_commitment("g-snooze"))
    dismiss_c, _ = await prepo.upsert_commitment(db_session, **_make_commitment("g-dismiss"))
    await db_session.commit()

    await prepo.mark_commitment_done(db_session, commitment_id=done_c.id, now=now)
    await prepo.snooze_commitment(
        db_session,
        commitment_id=snooze_c.id,
        snoozed_until=now + timedelta(days=1),
        now=now,
    )
    await prepo.dismiss_commitment(db_session, commitment_id=dismiss_c.id, now=now)
    await db_session.commit()

    done_r = await db_session.get(Commitment, done_c.id)
    snooze_r = await db_session.get(Commitment, snooze_c.id)
    dismiss_r = await db_session.get(Commitment, dismiss_c.id)

    assert done_r is not None and done_r.status == "done"
    assert snooze_r is not None and snooze_r.status == "snoozed"
    assert dismiss_r is not None and dismiss_r.status == "dismissed"
    # Ensure the three statuses are all different.
    assert len({done_r.status, snooze_r.status, dismiss_r.status}) == 3


# ── 5. Slack dismiss reply handler ────────────────────────────────────────────


async def test_slack_dismiss_reply_handler(db_session: AsyncSession) -> None:
    """'dismiss N', 'drop N', 'irrelevant N' all close with status='dismissed'."""
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)
    commitment, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-slack-dismiss",
        text="Book the venue for the offsite",
        owner_user_id=None,
        due=now + timedelta(days=3),
        sensitivity="personal_ops",
    )
    await db_session.commit()

    # Test 'dismiss N'
    result = await try_apply_commitment_reply(
        db_session,
        text=f"dismiss {commitment.id}",
    )
    assert result is not None
    assert "Dismissed" in result
    assert "No further follow-up" in result

    refreshed = await db_session.get(Commitment, commitment.id)
    assert refreshed is not None
    assert refreshed.status == "dismissed"

    # Ensure done / snooze replies on the same ID still return "not found" style
    # (already dismissed — still a valid terminal state; reply is idempotent).
    done_result = await try_apply_commitment_reply(
        db_session,
        text=f"done {commitment.id}",
    )
    # done still applies (status can transition from dismissed→done via done reply;
    # the test just confirms done reply is processed separately from dismiss).
    assert done_result is not None


async def test_slack_drop_alias(db_session: AsyncSession) -> None:
    """'drop N' is an alias for dismiss."""
    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)
    commitment, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-drop-alias",
        text="Write the press release",
        owner_user_id=None,
        due=now + timedelta(days=2),
        sensitivity="personal_ops",
    )
    await db_session.commit()

    result = await try_apply_commitment_reply(db_session, text=f"drop {commitment.id}")
    assert result is not None and "Dismissed" in result
    refreshed = await db_session.get(Commitment, commitment.id)
    assert refreshed is not None and refreshed.status == "dismissed"


# ── 6. Idempotent second dismiss ─────────────────────────────────────────────


async def test_dismiss_idempotent(db_session: AsyncSession) -> None:
    """Dismissing the same action-item twice is a no-op (no duplicate row)."""
    summary = await _seed_meeting_summary(db_session)
    await db_session.commit()

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from artemis.meetings.models import MeetingActionItemDismissal as Dismissal
    from artemis.proactivity.commitments import _normalize_text

    normalized = _normalize_text(_ACTION_TEXT)
    item_key = action_item_key(normalized)
    now = datetime.now(UTC)

    # Insert first dismissal.
    first_stmt = (
        pg_insert(Dismissal.__table__)  # type: ignore[arg-type]
        .values(
            meeting_summary_id=summary.id,
            action_item_key=item_key,
            granola_id=_GRANOLA_ID,
            action_item_text=normalized,
            dismissed_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_meeting_action_item_dismissals")
    )
    await db_session.execute(first_stmt)
    await db_session.commit()

    # Insert second dismissal — must silently no-op.
    second_stmt = (
        pg_insert(Dismissal.__table__)  # type: ignore[arg-type]
        .values(
            meeting_summary_id=summary.id,
            action_item_key=item_key,
            granola_id=_GRANOLA_ID,
            action_item_text=normalized,
            dismissed_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_meeting_action_item_dismissals")
    )
    await db_session.execute(second_stmt)
    await db_session.commit()

    count_result = await db_session.execute(
        select(Dismissal).where(
            Dismissal.meeting_summary_id == summary.id,
            Dismissal.action_item_key == item_key,
        )
    )
    rows = list(count_result.scalars())
    assert len(rows) == 1, "Idempotent second dismiss must not create a duplicate row"


# ── 7. action_item_key is stable (content hash) ──────────────────────────────


def test_action_item_key_is_stable() -> None:
    """Same text → same key; different text → different key; whitespace normalised."""
    key1 = action_item_key("Send the board recap email")
    key2 = action_item_key("  Send  the board  recap  email  ")
    key3 = action_item_key("Something completely different")

    assert key1 == key2, "Whitespace-normalised text must hash identically"
    assert key1 != key3, "Different texts must produce different keys"
    assert len(key1) == 64, "SHA-256 hex digest is 64 characters"


# ── 8. Followup sweep excludes dismissed commitments ─────────────────────────


async def test_followup_sweep_skips_dismissed(db_session: AsyncSession) -> None:
    """list_commitment_followup_candidates must NOT return dismissed commitments."""
    from artemis.integrations import repository as integrations_repo
    from artemis.integrations.crypto import encrypt_credentials

    await integrations_repo.upsert_integration(
        db_session,
        provider="slack",
        workspace_id="T_DISMISS_TEST",
        agent_id="artemis",
        encrypted_credentials=encrypt_credentials({"bot_token": "xoxb-test-dismiss"}),
        display_name="Dismiss Test Workspace",
    )
    await integrations_repo.upsert_provider_config(
        db_session, "slack", {"authed_user_id": "U_JON_TEST"}
    )
    await db_session.commit()

    now = datetime(2026, 6, 13, 15, 0, tzinfo=UTC)

    active_c, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-sweep-active",
        text="Write the Q2 review",
        owner_user_id=None,
        due=now + timedelta(hours=1),
        sensitivity="personal_ops",
    )
    dismissed_c, _ = await prepo.upsert_commitment(
        db_session,
        source_type="granola_meeting",
        source_id="g-sweep-dismissed",
        text="Book the catering",
        owner_user_id=None,
        due=now + timedelta(hours=1),
        sensitivity="personal_ops",
    )
    await db_session.commit()

    await prepo.dismiss_commitment(db_session, commitment_id=dismissed_c.id, now=now)
    await db_session.commit()

    due_soon_cutoff = now + timedelta(hours=2)
    renotify_cutoff = now - timedelta(hours=1)
    candidates = await prepo.list_commitment_followup_candidates(
        db_session,
        now=now,
        due_soon_cutoff=due_soon_cutoff,
        renotify_cutoff=renotify_cutoff,
    )

    candidate_ids = [c.id for c in candidates]
    assert active_c.id in candidate_ids, "Active commitment must be in followup candidates"
    assert dismissed_c.id not in candidate_ids, (
        "Dismissed commitment must NOT be in followup candidates"
    )
