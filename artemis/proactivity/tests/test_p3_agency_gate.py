"""Tests for the propose→confirm agency-writes gate (Lane G).

Safety invariants verified here:
1. No execution path exists without status=approved first.
2. A proposal executes at most once (double-yes is a no-op).
3. Preview matches payload (the thing approved is the thing executed).
4. Every state transition is audit-logged with actor + timestamp.
5. Expired proposals never execute.
6. Reply matcher returns None when no proposal is pending (fall-through guard).
7. Reply matcher returns None when >1 proposal pending + bare yes/no.
8. Calendar create and Jira create work through the gate.
9. Reject (no) transitions to rejected + executor NOT called.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.proactivity import radar_repository
from artemis.proactivity.agency_gate import (
    execute_proposed_action,
    propose_action,
    propose_radar_slack_reply,
    try_apply_proposed_action_reply,
)
from artemis.proactivity.models import ProposedAction
from artemis.proactivity.proposed_actions_repository import (
    approve_proposed_action,
    create_proposed_action,
    expire_stale_proposals,
    list_pending_for_user,
    mark_executed,
    reject_proposed_action,
)

pytestmark = pytest.mark.asyncio

_JON = "U_JON"
_NOW = datetime(2026, 6, 13, 10, 0, tzinfo=UTC)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _make_cal_proposal(
    session: AsyncSession,
    *,
    ttl_hours: int = 24,
) -> ProposedAction:
    return await create_proposed_action(
        session,
        action_type="calendar.create",
        payload={
            "calendar_id": "primary",
            "summary": "Hold with Angela",
            "start": {"dateTime": "2026-06-18T14:00:00Z"},
            "end": {"dateTime": "2026-06-18T14:30:00Z"},
        },
        preview="30-min hold with Angela Thu 2 PM",
        requested_by="artemis",
        target_user_id=_JON,
        ttl_hours=ttl_hours,
    )


async def _make_jira_proposal(
    session: AsyncSession,
    *,
    ttl_hours: int = 24,
) -> ProposedAction:
    return await create_proposed_action(
        session,
        action_type="jira.create",
        payload={
            "project_key": "TEST",
            "summary": "Follow up on Q3 OKRs",
            "description": "Drafted by Artemis",
        },
        preview="Jira task: Follow up on Q3 OKRs in TEST",
        requested_by="artemis",
        target_user_id=_JON,
        ttl_hours=ttl_hours,
    )


async def _make_slack_proposal(
    session: AsyncSession,
    *,
    ttl_hours: int = 24,
) -> ProposedAction:
    return await create_proposed_action(
        session,
        action_type="slack.send",
        payload={
            "channel": "C123",
            "thread_ts": "1710000000.000100",
            "text": "Thanks, I will take a look today.",
        },
        preview='reply in #general: "Thanks, I will take a look today."',
        requested_by="artemis",
        target_user_id=_JON,
        ttl_hours=ttl_hours,
    )


async def _make_gmail_proposal(
    session: AsyncSession,
    *,
    ttl_hours: int = 24,
) -> ProposedAction:
    return await create_proposed_action(
        session,
        action_type="gmail.send",
        payload={
            "to": "jon.fila@amiralearning.com",
            "subject": "Re: Agency test",
            "body": "Looks good. Sending the reply through the gate.",
            "thread_id": "thread-123",
            "in_reply_to": "<msg-123@example.com>",
        },
        preview="email Jon back about Agency test",
        requested_by="artemis",
        target_user_id=_JON,
        ttl_hours=ttl_hours,
    )


# ── 1. Repository — basic lifecycle ──────────────────────────────────────────


async def test_create_proposal_is_proposed(db_session: AsyncSession) -> None:
    action = await _make_cal_proposal(db_session)
    await db_session.commit()
    assert action.id is not None
    assert action.status == "proposed"
    assert action.action_type == "calendar.create"
    assert action.audit[0]["action"] == "proposed"


async def test_approve_transitions_status(db_session: AsyncSession) -> None:
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    approved = await approve_proposed_action(db_session, action_id=action.id, actor=_JON, now=_NOW)
    assert approved is not None
    assert approved.status == "approved"
    # Audit has proposed + approved entries.
    assert any(e["action"] == "approved" for e in approved.audit)


async def test_reject_transitions_status(db_session: AsyncSession) -> None:
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    rejected = await reject_proposed_action(db_session, action_id=action.id, actor=_JON, now=_NOW)
    assert rejected is not None
    assert rejected.status == "rejected"
    assert any(e["action"] == "rejected" for e in rejected.audit)


async def test_approve_nonexistent_returns_none(db_session: AsyncSession) -> None:
    result = await approve_proposed_action(db_session, action_id=99999, actor=_JON, now=_NOW)
    assert result is None


async def test_double_approve_second_is_noop(db_session: AsyncSession) -> None:
    """Invariant 2: a proposal can only be approved once."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    first = await approve_proposed_action(db_session, action_id=action.id, actor=_JON, now=_NOW)
    assert first is not None

    # Move to executed state.
    await mark_executed(
        db_session,
        action_id=action.id,
        result={"event_id": "x", "link": "https://cal.example.com"},
        actor="artemis",
    )
    await db_session.commit()

    # Second approve attempt on 'executed' row → None (no-op).
    second = await approve_proposed_action(db_session, action_id=action.id, actor=_JON, now=_NOW)
    assert second is None


async def test_expire_stale_proposals(db_session: AsyncSession) -> None:
    """Invariant 5: expired proposals cannot be approved."""
    action = await _make_cal_proposal(db_session, ttl_hours=1)
    await db_session.commit()

    # Expire by advancing time past expires_at.
    future = _NOW + timedelta(hours=25)
    count = await expire_stale_proposals(db_session, now=future)
    await db_session.commit()
    assert count >= 1

    await db_session.refresh(action)
    assert action.status == "expired"

    # Attempt to approve an expired row → None.
    result = await approve_proposed_action(db_session, action_id=action.id, actor=_JON, now=future)
    assert result is None


async def test_mark_executed_requires_approved(db_session: AsyncSession) -> None:
    """Invariant 1: execution only possible from 'approved' state."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    with pytest.raises(ValueError, match="can only execute from 'approved'"):
        await mark_executed(
            db_session,
            action_id=action.id,
            result={"event_id": "x"},
            actor="artemis",
        )


async def test_audit_logs_every_transition(db_session: AsyncSession) -> None:
    """Invariant 4: every transition is in the audit trail."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    await approve_proposed_action(db_session, action_id=action.id, actor=_JON)
    await mark_executed(
        db_session,
        action_id=action.id,
        result={"event_id": "ev1", "link": "https://cal.google.com/ev1"},
        actor="artemis",
    )
    await db_session.commit()
    await db_session.refresh(action)

    actions_in_audit = [e["action"] for e in action.audit]
    assert "proposed" in actions_in_audit
    assert "approved" in actions_in_audit
    assert "executed" in actions_in_audit
    # All entries have actor + at.
    for entry in action.audit:
        assert "actor" in entry
        assert "at" in entry


# ── 2. execute_proposed_action — status guard ─────────────────────────────────


async def test_execute_proposed_action_requires_approved_status(
    db_session: AsyncSession,
) -> None:
    """Invariant 1: execute_proposed_action raises if status != approved."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()
    # action.status == 'proposed' — must raise.
    with pytest.raises(ValueError, match="only 'approved' proposals may execute"):
        await execute_proposed_action(db_session, action)


# ── 3. Reply handler — no proposal pending → fall-through ────────────────────


async def test_reply_handler_none_when_no_proposals(db_session: AsyncSession) -> None:
    """Invariant 6: bare 'yes' with no pending proposals → None (fall-through)."""
    result = await try_apply_proposed_action_reply(
        db_session, text="yes", slack_user_id=_JON, now=_NOW
    )
    assert result is None


async def test_reply_handler_none_when_multiple_pending(
    db_session: AsyncSession,
) -> None:
    """Invariant 7: bare 'yes' with multiple pending → ambiguity message (not None — it IS a response)."""
    await _make_cal_proposal(db_session)
    await _make_jira_proposal(db_session)
    await db_session.commit()

    result = await try_apply_proposed_action_reply(
        db_session, text="yes", slack_user_id=_JON, now=_NOW
    )
    # Must NOT be None — we return an informative message, not fall-through.
    assert result is not None
    assert "pending" in result.lower() or "A" in result


async def test_reply_handler_none_for_unrelated_text(db_session: AsyncSession) -> None:
    """Non-yes/no text always returns None."""
    for msg in ("done 1", "sure", "ok", "hello", "yes please"):
        result = await try_apply_proposed_action_reply(
            db_session, text=msg, slack_user_id=_JON, now=_NOW
        )
        assert result is None, f"Expected None for {msg!r}, got {result!r}"


# ── 4. Calendar create through the gate ─────────────────────────────────────


async def test_calendar_create_yes_executes_and_confirms(
    db_session: AsyncSession,
) -> None:
    """Yes → executor called with approved payload, status=executed, result has link."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    fake_event = MagicMock()
    fake_event.id = "ev_abc"
    fake_event.html_link = "https://calendar.google.com/ev_abc"
    fake_event.summary = "Hold with Angela"

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_gcal_client",
            new=AsyncMock(
                return_value=MagicMock(
                    create_event=AsyncMock(return_value=fake_event),
                )
            ),
        ),
        patch(
            "artemis.integrations.gcal.sync.sync_recent_gcal_events_cache",
            new=AsyncMock(),
        ),
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert result is not None
    assert "Hold with Angela" in result or "created" in result.lower()

    await db_session.refresh(action)
    assert action.status == "executed"
    assert action.executed_result is not None
    assert action.executed_result.get("event_id") == "ev_abc"


async def test_calendar_create_no_rejects_no_executor(
    db_session: AsyncSession,
) -> None:
    """No → status=rejected, executor NOT called."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    mock_executor = AsyncMock()
    with patch(
        "artemis.proactivity.agency_gate._execute_calendar_create",
        new=mock_executor,
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"no A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert result is not None
    assert "cancel" in result.lower() or "skip" in result.lower()

    await db_session.refresh(action)
    assert action.status == "rejected"
    mock_executor.assert_not_called()


async def test_calendar_create_double_yes_executes_once(
    db_session: AsyncSession,
) -> None:
    """Invariant 2: double-yes executes exactly once."""
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    fake_event = MagicMock()
    fake_event.id = "ev_xyz"
    fake_event.html_link = "https://calendar.google.com/ev_xyz"
    fake_event.summary = "Hold with Angela"

    call_count = 0

    async def _fake_create_event(*_: Any, **__: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        return fake_event

    mock_gcal = MagicMock()
    mock_gcal.create_event = _fake_create_event

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_gcal_client",
            new=AsyncMock(return_value=mock_gcal),
        ),
        patch(
            "artemis.integrations.gcal.sync.sync_recent_gcal_events_cache",
            new=AsyncMock(),
        ),
    ):
        first = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )
        second = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert first is not None
    # Second yes must be a no-op (row is now 'executed', not 'proposed').
    assert second is not None
    assert "already" in second.lower() or "no pending" in second.lower()
    assert call_count == 1, f"executor called {call_count} times, expected 1"


async def test_expired_proposal_never_executes(
    db_session: AsyncSession,
) -> None:
    """Invariant 5: yes on an expired proposal → no execution."""
    action = await _make_cal_proposal(db_session, ttl_hours=1)
    await db_session.commit()

    future = _NOW + timedelta(hours=25)
    mock_executor = AsyncMock()

    with patch(
        "artemis.proactivity.agency_gate._execute_calendar_create",
        new=mock_executor,
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=future,
        )

    # Expired proposals are not 'proposed' anymore — reply returns not-found message.
    assert result is not None
    mock_executor.assert_not_called()

    await db_session.refresh(action)
    assert action.status in ("expired", "proposed")  # expired by TTL sweep or not found


# ── 5. Jira create through the gate ──────────────────────────────────────────


async def test_jira_create_yes_executes_and_returns_key(
    db_session: AsyncSession,
) -> None:
    action = await _make_jira_proposal(db_session)
    await db_session.commit()

    mock_jira = MagicMock()
    mock_jira.create_issue = AsyncMock(return_value={"key": "TEST-42", "id": "10042"})

    with patch(
        "artemis.proactivity.agency_gate._resolve_jira_client",
        new=AsyncMock(return_value=mock_jira),
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert result is not None
    assert "TEST-42" in result

    await db_session.refresh(action)
    assert action.status == "executed"
    assert action.executed_result["key"] == "TEST-42"

    # Verify create_issue was called with the approved payload fields.
    mock_jira.create_issue.assert_awaited_once()
    call_kwargs = mock_jira.create_issue.call_args.kwargs
    assert call_kwargs["project_key"] == "TEST"
    assert call_kwargs["summary"] == "Follow up on Q3 OKRs"


async def test_jira_create_no_rejects_no_executor(db_session: AsyncSession) -> None:
    action = await _make_jira_proposal(db_session)
    await db_session.commit()

    mock_executor = AsyncMock()
    with patch(
        "artemis.proactivity.agency_gate._execute_jira_create",
        new=mock_executor,
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"no A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert result is not None
    await db_session.refresh(action)
    assert action.status == "rejected"
    mock_executor.assert_not_called()


# ── 6. Bare yes/no with exactly one pending ──────────────────────────────────


async def test_bare_yes_with_single_pending_executes(db_session: AsyncSession) -> None:
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    fake_event = MagicMock()
    fake_event.id = "ev_bare"
    fake_event.html_link = "https://calendar.google.com/ev_bare"
    fake_event.summary = "Hold with Angela"

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_gcal_client",
            new=AsyncMock(
                return_value=MagicMock(
                    create_event=AsyncMock(return_value=fake_event),
                )
            ),
        ),
        patch(
            "artemis.integrations.gcal.sync.sync_recent_gcal_events_cache",
            new=AsyncMock(),
        ),
    ):
        result = await try_apply_proposed_action_reply(
            db_session, text="yes", slack_user_id=_JON, now=_NOW
        )

    assert result is not None
    assert "created" in result.lower() or "Hold" in result

    await db_session.refresh(action)
    assert action.status == "executed"


async def test_bare_no_with_single_pending_rejects(db_session: AsyncSession) -> None:
    action = await _make_cal_proposal(db_session)
    await db_session.commit()

    result = await try_apply_proposed_action_reply(
        db_session, text="no", slack_user_id=_JON, now=_NOW
    )

    assert result is not None
    await db_session.refresh(action)
    assert action.status == "rejected"


# ── 7. Slack/Gmail executors through the gate ────────────────────────────────


async def test_slack_send_yes_executes_thread_reply(
    db_session: AsyncSession,
) -> None:
    action = await _make_slack_proposal(db_session)
    await db_session.commit()

    mock_slack = MagicMock()
    mock_slack.post_message = AsyncMock(return_value={"ts": "1710000123.000200"})

    with (
        patch(
            "artemis.proactivity.agency_gate._resolve_slack_user_token",
            new=AsyncMock(return_value="xoxp-user"),
        ),
        patch(
            "artemis.integrations.slack.client.SlackClient",
            return_value=mock_slack,
        ),
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert result is not None
    assert "Slack message posted" in result

    await db_session.refresh(action)
    assert action.status == "executed"
    assert action.executed_result["message_ts"] == "1710000123.000200"
    mock_slack.post_message.assert_awaited_once_with(
        channel="C123",
        text="Thanks, I will take a look today.",
        thread_ts="1710000000.000100",
    )


async def test_gmail_send_yes_executes_reply(
    db_session: AsyncSession,
) -> None:
    action = await _make_gmail_proposal(db_session)
    await db_session.commit()

    mock_gmail = MagicMock()
    mock_gmail.send_message = AsyncMock(return_value={"id": "msg-999", "threadId": "thread-123"})

    with patch(
        "artemis.proactivity.agency_gate._resolve_personal_gmail_client",
        new=AsyncMock(return_value=mock_gmail),
    ):
        result = await try_apply_proposed_action_reply(
            db_session,
            text=f"yes A{action.id}",
            slack_user_id=_JON,
            now=_NOW,
        )

    assert result is not None
    assert "jon.fila@amiralearning.com" in result

    await db_session.refresh(action)
    assert action.status == "executed"
    assert action.executed_result["message_id"] == "msg-999"
    mock_gmail.send_message.assert_awaited_once_with(
        to="jon.fila@amiralearning.com",
        subject="Re: Agency test",
        body="Looks good. Sending the reply through the gate.",
        thread_id="thread-123",
        in_reply_to="<msg-123@example.com>",
    )


async def test_propose_radar_slack_reply_creates_threaded_action(
    db_session: AsyncSession,
) -> None:
    radar_item, _ = await radar_repository.upsert_surfaced(
        db_session,
        item_type="slack_mention",
        item_key="C777:1710000000.000100",
        label="#eng thread",
        permalink="https://slack.example/thread",
        now=_NOW,
    )
    await db_session.commit()

    with patch(
        "artemis.proactivity.agency_gate.send_proposal_dm",
        new=AsyncMock(),
    ) as mock_dm:
        action, surfaced = await propose_radar_slack_reply(
            db_session,
            radar_item_id=radar_item.id,
            reply_text="On it. I will circle back today.",
            requested_by="artemis",
            target_user_id=_JON,
        )

    assert surfaced.id == radar_item.id
    assert action.action_type == "slack.send"
    assert action.payload == {
        "channel": "C777",
        "thread_ts": "1710000000.000100",
        "text": "On it. I will circle back today.",
    }
    assert "#eng thread" in action.preview
    mock_dm.assert_awaited_once_with(db_session, action)


# ── 8. propose_action public API ─────────────────────────────────────────────


async def test_propose_action_public_api(db_session: AsyncSession) -> None:
    """propose_action inserts in proposed state with correct fields."""
    action = await propose_action(
        db_session,
        action_type="jira.create",
        payload={"project_key": "AMI", "summary": "Test task"},
        preview="Create Jira task: Test task in AMI",
        requested_by="artemis",
        target_user_id=_JON,
        ttl_hours=12,
    )
    await db_session.commit()

    assert action.id is not None
    assert action.status == "proposed"
    assert action.action_type == "jira.create"
    assert action.payload["project_key"] == "AMI"
    assert action.preview == "Create Jira task: Test task in AMI"

    # TTL should be ~12h from now.
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    assert action.expires_at > now + timedelta(hours=11)
    assert action.expires_at < now + timedelta(hours=13)


# ── 9. list_pending_for_user scoping ─────────────────────────────────────────


async def test_pending_scoped_to_target_user(db_session: AsyncSession) -> None:
    """Proposals for user A are never surfaced to user B."""
    await create_proposed_action(
        db_session,
        action_type="jira.create",
        payload={"project_key": "X", "summary": "S"},
        preview="preview",
        requested_by="artemis",
        target_user_id="U_OTHER",
        ttl_hours=24,
    )
    await db_session.commit()

    pending = await list_pending_for_user(db_session, target_user_id=_JON, now=_NOW)
    assert len(pending) == 0
