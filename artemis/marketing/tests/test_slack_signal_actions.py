"""Approve / reject a signal from the card, and who is allowed to.

3,479 signals sat at `qualified` averaging 40 days old against 58 ever approved,
because the notification arrived in Slack and the decision lived in the web app.
These buttons close that gap WITHOUT letting an agent decide anything: the card
is a proposal, a named human clicks, Slack signs the click.
"""

from __future__ import annotations

import json

import pytest

from artemis.marketing.slack_signal_actions import (
    SIGNAL_ACTION_IDS,
    SIGNAL_APPROVE,
    SIGNAL_REJECT,
    _parse_signal_id,
    handle_signal_block_action,
    is_authorized,
)


def _body(resp: object) -> str:
    return bytes(getattr(resp, "body", b"")).decode()


# ── who may decide ───────────────────────────────────────────────────────────


def test_an_unknown_email_cannot_decide() -> None:
    """Fail closed. The allowlist is a config value, not a code literal."""
    assert is_authorized("someone.random@example.com") is False


def test_no_email_at_all_cannot_decide() -> None:
    """An unresolvable clicker is not an authorized one."""
    assert is_authorized(None) is False
    assert is_authorized("") is False


def test_the_configured_approvers_can() -> None:
    from artemis.config import settings

    first = settings.signal_approver_emails.split(",")[0].strip()

    assert is_authorized(first) is True
    assert is_authorized(first.upper()) is True, "email comparison must be case-insensitive"


@pytest.mark.asyncio
async def test_an_unidentified_clicker_is_told_it_is_identity_not_permission() -> None:
    """The crisis-content approval bug sent four people hunting an allowlist for
    what was a NULL slack_user_id. Fail closed, but say WHICH."""
    resp = await handle_signal_block_action(
        None,  # type: ignore[arg-type]
        action_id=SIGNAL_APPROVE,
        value="signal:1",
        decided_by="slack:U123",
        resolved_email=None,
    )
    text = json.loads(_body(resp))["text"]

    assert "could not work out who you are" in text
    assert "NOT a permissions refusal" in text


@pytest.mark.asyncio
async def test_an_unauthorized_clicker_changes_nothing() -> None:
    resp = await handle_signal_block_action(
        None,  # type: ignore[arg-type]
        action_id=SIGNAL_APPROVE,
        value="signal:1",
        decided_by="nobody@example.com",
        resolved_email="nobody@example.com",
    )
    text = json.loads(_body(resp))["text"]

    assert "not on the signal approver list" in text
    assert "nothing" in text.lower()


# ── the button's value is not trusted for anything that matters ──────────────


def test_the_value_carries_only_a_signal_id() -> None:
    assert _parse_signal_id("signal:4211") == 4211
    assert _parse_signal_id("4211") is None
    assert _parse_signal_id("signal:not-a-number") is None
    assert _parse_signal_id("") is None


@pytest.mark.asyncio
async def test_a_malformed_value_is_refused_before_anything_else() -> None:
    """Checked before authorization, so a broken button cannot reach the DB."""
    resp = await handle_signal_block_action(
        None,  # type: ignore[arg-type]
        action_id=SIGNAL_APPROVE,
        value="nonsense",
        decided_by="jon.fila@amiralearning.com",
        resolved_email="jon.fila@amiralearning.com",
    )

    assert "malformed" in json.loads(_body(resp))["text"]


# ── it does not reimplement the decision ─────────────────────────────────────


def test_it_calls_the_same_implementation_the_web_queue_calls() -> None:
    """`promote_signal_to_candidate`'s own docstring says both existing paths
    share it "so side effects cannot drift". A third path that promoted signals
    itself would drift by the end of the week."""
    import inspect

    from artemis.marketing import slack_signal_actions as mod

    src = inspect.getsource(mod.handle_signal_block_action)
    assert "approve_signal_impl" in src
    assert "reject_signal_impl" in src
    assert "promote_signal_to_candidate" not in src, "must not promote on its own"


def test_decided_by_is_not_a_request_parameter() -> None:
    """Adding it to the route signature would make it a QUERY parameter, so
    anyone could name whoever they liked as the decider — the same flaw that
    makes `decide_approval` unsafe."""
    import inspect

    from artemis.marketing.routes.signal_queue import approve_signal, approve_signal_impl

    assert "decided_by" not in inspect.signature(approve_signal).parameters
    assert "decided_by" in inspect.signature(approve_signal_impl).parameters


# ── the card ─────────────────────────────────────────────────────────────────


def test_the_card_carries_both_buttons_and_only_the_id() -> None:
    from artemis.marketing.callie_push import _decision_blocks

    blocks = _decision_blocks("A signal happened", 4211)
    actions = next(b for b in blocks if b["type"] == "actions")  # type: ignore[index]
    ids = {e["action_id"] for e in actions["elements"]}  # type: ignore[index]
    values = {e["value"] for e in actions["elements"]}  # type: ignore[index]

    assert ids == {SIGNAL_APPROVE, SIGNAL_REJECT}
    assert values == {"signal:4211"}, "the value must carry the id and nothing else"
    assert ids == set(SIGNAL_ACTION_IDS)


def test_the_card_still_carries_its_text() -> None:
    """The blocks replace the rendering, so the body must survive them."""
    from artemis.marketing.callie_push import _decision_blocks

    blocks = _decision_blocks("Pinellas County Schools RFP", 1)
    section = next(b for b in blocks if b["type"] == "section")  # type: ignore[index]

    assert "Pinellas County Schools RFP" in section["text"]["text"]  # type: ignore[index]


# ── the effect, against a real database ──────────────────────────────────────


@pytest.mark.asyncio
async def test_an_authorized_click_actually_promotes_the_signal(db_session) -> None:
    """The acceptance criterion is the DB effect, never the returned text.

    `approve_signal` (the layer-3 agent tool) flips a status and promotes
    nothing, which is how a signal can read as approved while no candidate
    exists. This path must do the real thing.
    """
    from sqlalchemy import select

    from artemis.config import settings
    from artemis.marketing.models import CampaignCandidateSignal, SignalQueue
    from artemis.marketing.repository import create_signal, update_signal

    approver = settings.signal_approver_emails.split(",")[0].strip()
    signal = await create_signal(
        db_session,
        headline="A district put out a literacy RFP",
        campaign_family="test_family",
        source_type="manual",
        summary="seeded",
        discovered_by="manual",
    )
    await update_signal(db_session, signal.id, signal_status="qualified")
    await db_session.commit()

    resp = await handle_signal_block_action(
        db_session,
        action_id=SIGNAL_APPROVE,
        value=f"signal:{signal.id}",
        decided_by=approver,
        resolved_email=approver,
    )

    assert "approved" in json.loads(_body(resp))["text"]

    refreshed = await db_session.get(SignalQueue, signal.id)
    assert refreshed is not None
    assert refreshed.signal_status == "approved"

    # The candidate is the part the agent tool skips.
    link = (
        await db_session.execute(
            select(CampaignCandidateSignal).where(CampaignCandidateSignal.signal_id == signal.id)
        )
    ).scalar_one_or_none()
    assert link is not None, "approval must promote to a campaign candidate"


@pytest.mark.asyncio
async def test_a_second_click_says_someone_got_here_first(db_session) -> None:
    """Two people reading the same card is the normal case, not an error."""
    from artemis.config import settings
    from artemis.marketing.repository import create_signal, update_signal

    approver = settings.signal_approver_emails.split(",")[0].strip()
    signal = await create_signal(
        db_session,
        headline="Double-clicked signal",
        campaign_family="test_family",
        source_type="manual",
        summary="seeded",
        discovered_by="manual",
    )
    await update_signal(db_session, signal.id, signal_status="qualified")
    await db_session.commit()

    first = await handle_signal_block_action(
        db_session,
        action_id=SIGNAL_APPROVE,
        value=f"signal:{signal.id}",
        decided_by=approver,
        resolved_email=approver,
    )
    second = await handle_signal_block_action(
        db_session,
        action_id=SIGNAL_APPROVE,
        value=f"signal:{signal.id}",
        decided_by=approver,
        resolved_email=approver,
    )

    assert "approved" in json.loads(_body(first))["text"]
    assert "not changed" in json.loads(_body(second))["text"]
    assert "got here first" in json.loads(_body(second))["text"]


@pytest.mark.asyncio
async def test_rejecting_records_the_person_who_rejected(db_session) -> None:
    from artemis.config import settings
    from artemis.marketing.models import SignalQueue
    from artemis.marketing.repository import create_signal, update_signal

    approver = settings.signal_approver_emails.split(",")[0].strip()
    signal = await create_signal(
        db_session,
        headline="Not relevant to us",
        campaign_family="test_family",
        source_type="manual",
        summary="seeded",
        discovered_by="manual",
    )
    await update_signal(db_session, signal.id, signal_status="qualified")
    await db_session.commit()

    resp = await handle_signal_block_action(
        db_session,
        action_id=SIGNAL_REJECT,
        value=f"signal:{signal.id}",
        decided_by=approver,
        resolved_email=approver,
    )

    assert approver in json.loads(_body(resp))["text"]
    refreshed = await db_session.get(SignalQueue, signal.id)
    assert refreshed is not None
    assert refreshed.signal_status == "rejected_at_gate_1"


def test_the_dispatch_tables_do_not_collide() -> None:
    """Three independent tables key off `action_id` in one route. An id in two
    of them routes to whichever branch is checked first — silently, and to the
    wrong handler."""
    from artemis.crisis_content.slack_actions import CRISIS_CONTENT_ACTION_IDS
    from artemis.routes.integrations_slack_interactivity import _APPROVAL_ACTION_IDS

    assert not SIGNAL_ACTION_IDS & set(CRISIS_CONTENT_ACTION_IDS)
    assert not SIGNAL_ACTION_IDS & set(_APPROVAL_ACTION_IDS)


def test_the_route_dispatches_signal_clicks() -> None:
    """A handler no route reaches is the shape this codebase keeps producing."""
    import inspect

    from artemis.routes import integrations_slack_interactivity as route

    src = inspect.getsource(route.slack_interactivity)
    assert "SIGNAL_ACTION_IDS" in src
    assert "handle_signal_block_action" in src
    # Authorization must come from the resolved email, never the click's value.
    assert "resolved_email=resolved_email" in src
