"""Tests for the natural pending-context conversation router."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.proactivity.models import OkrCheckinBreadcrumb, ProposedAction
from artemis.proactivity.natural_conversation import route_pending_reply
from artemis.proactivity.proposed_actions_repository import create_proposed_action
from artemis.proactivity.repository import (
    create_okr_checkin_breadcrumb,
    get_live_okr_checkin_breadcrumb,
)

pytestmark = pytest.mark.asyncio

_JON = "U_JON_NATURAL"


async def _make_proposal(
    session: AsyncSession,
    *,
    action_type: str,
    preview: str,
) -> int:
    action = await create_proposed_action(
        session,
        action_type=action_type,
        payload={"stub": True},
        preview=preview,
        requested_by="artemis",
        target_user_id=_JON,
    )
    await session.commit()
    return int(action.id)


async def _make_breadcrumb(session: AsyncSession) -> int:
    crumb = await create_okr_checkin_breadcrumb(
        session,
        recipient_id=_JON,
        kr_snapshot=[
            {
                "kr_id": 12,
                "kr_title": "Improve approval speed",
                "objective_title": "Workflow quality",
                "prog": 50,
                "target_text": "100%",
            }
        ],
        proposal_text="Friday OKR check-in",
        expires_at=datetime.now(UTC) + timedelta(days=3),
    )
    crumb.staged_updates = [
        {
            "kr_id": 12,
            "progress": 65,
            "basis": "Shipped the approval gate cleanup.",
            "bullet": "Shipped the approval gate cleanup.",
        }
    ]
    await session.commit()
    return int(crumb.id)


async def test_yes_a_ids_approves_both_proposals_without_touching_staged_okr(
    db_session: AsyncSession,
) -> None:
    slack_id = await _make_proposal(
        db_session,
        action_type="slack.send",
        preview='reply in #ops: "Looks good."',
    )
    email_id = await _make_proposal(
        db_session,
        action_type="gmail.send",
        preview="email Angela back about the draft",
    )
    crumb_id = await _make_breadcrumb(db_session)

    adapter = FakeAdapter(
        [
            ScriptedReply(
                text=(
                    "{"
                    f'"intent":"approve_proposals","proposal_ids":[{slack_id},{email_id}],'
                    '"confidence":0.99,"reply_text":"Done. I handled both.","reason":"explicit ids"}'
                )
            )
        ]
    )

    async def _fake_execute(_session: AsyncSession, action: object) -> dict[str, str]:
        return {"summary": getattr(action, "action_type", "ok")}

    with patch(
        "artemis.proactivity.agency_gate.execute_proposed_action",
        new=AsyncMock(side_effect=_fake_execute),
    ):
        outcome = await route_pending_reply(
            db_session,
            session_id="slack-artemis-T1-D1-_",
            slack_user_id=_JON,
            text=f"yes a{slack_id} and a{email_id}",
            adapter=adapter,
        )

    assert outcome.handled is True
    assert outcome.intent == "approve_proposals"
    assert outcome.outbound_text == "Done. I handled both."

    slack_row = await db_session.get(ProposedAction, slack_id)
    email_row = await db_session.get(ProposedAction, email_id)
    assert slack_row is not None and slack_row.status == "executed"
    assert email_row is not None and email_row.status == "executed"

    crumb = await db_session.get(OkrCheckinBreadcrumb, crumb_id)
    assert crumb is not None and crumb.staged_updates, "OKR staged updates must remain intact"


async def test_go_ahead_with_the_slack_one_targets_only_slack_proposal(
    db_session: AsyncSession,
) -> None:
    slack_id = await _make_proposal(
        db_session,
        action_type="slack.send",
        preview='reply in #ops: "Looks good."',
    )
    email_id = await _make_proposal(
        db_session,
        action_type="gmail.send",
        preview="email Angela back about the draft",
    )

    adapter = FakeAdapter(
        [
            ScriptedReply(
                text=(
                    "{"
                    f'"intent":"approve_proposals","proposal_ids":[{slack_id}],'
                    '"confidence":0.98,"reply_text":"Done. I sent the Slack note.","reason":"slack one"}'
                )
            )
        ]
    )

    with patch(
        "artemis.proactivity.agency_gate.execute_proposed_action",
        new=AsyncMock(return_value={"summary": "slack.send"}),
    ):
        outcome = await route_pending_reply(
            db_session,
            session_id="slack-artemis-T1-D1-_",
            slack_user_id=_JON,
            text="go ahead with the Slack one",
            adapter=adapter,
        )

    assert outcome.handled is True
    assert outcome.intent == "approve_proposals"
    assert outcome.outbound_text == "Done. I sent the Slack note."

    slack_row = await db_session.get(ProposedAction, slack_id)
    email_row = await db_session.get(ProposedAction, email_id)
    assert slack_row is not None and slack_row.status == "executed"
    assert email_row is not None and email_row.status == "proposed"


async def test_skip_the_email_rejects_only_email_proposal(db_session: AsyncSession) -> None:
    slack_id = await _make_proposal(
        db_session,
        action_type="slack.send",
        preview='reply in #ops: "Looks good."',
    )
    email_id = await _make_proposal(
        db_session,
        action_type="gmail.send",
        preview="email Angela back about the draft",
    )

    adapter = FakeAdapter(
        [
            ScriptedReply(
                text=(
                    "{"
                    f'"intent":"reject_proposals","proposal_ids":[{email_id}],'
                    '"confidence":0.97,"reply_text":"Okay. I skipped the email.","reason":"email skip"}'
                )
            )
        ]
    )

    outcome = await route_pending_reply(
        db_session,
        session_id="slack-artemis-T1-D1-_",
        slack_user_id=_JON,
        text="skip the email",
        adapter=adapter,
    )

    assert outcome.handled is True
    assert outcome.intent == "reject_proposals"
    assert outcome.outbound_text == "Okay. I skipped the email."

    slack_row = await db_session.get(ProposedAction, slack_id)
    email_row = await db_session.get(ProposedAction, email_id)
    assert slack_row is not None and slack_row.status == "proposed"
    assert email_row is not None and email_row.status == "rejected"


async def test_ambiguous_yes_across_proposals_and_okr_clarifies_without_action(
    db_session: AsyncSession,
) -> None:
    slack_id = await _make_proposal(
        db_session,
        action_type="slack.send",
        preview='reply in #ops: "Looks good."',
    )
    email_id = await _make_proposal(
        db_session,
        action_type="gmail.send",
        preview="email Angela back about the draft",
    )
    await _make_breadcrumb(db_session)

    adapter = FakeAdapter(
        [
            ScriptedReply(
                text=(
                    '{"intent":"clarify","proposal_ids":[],"confidence":0.75,'
                    '"reply_text":"Did you mean the pending proposals, or the staged OKR updates?",'
                    '"reason":"ambiguous bare yes"}'
                )
            )
        ]
    )

    outcome = await route_pending_reply(
        db_session,
        session_id="slack-artemis-T1-D1-_",
        slack_user_id=_JON,
        text="yes",
        adapter=adapter,
    )

    assert outcome.handled is True
    assert outcome.intent == "clarify"
    assert "OKR" in str(outcome.outbound_text)

    slack_row = await db_session.get(ProposedAction, slack_id)
    email_row = await db_session.get(ProposedAction, email_id)
    assert slack_row is not None and slack_row.status == "proposed"
    assert email_row is not None and email_row.status == "proposed"

    live_crumb = await get_live_okr_checkin_breadcrumb(db_session, _JON)
    assert live_crumb is not None and live_crumb.staged_updates
