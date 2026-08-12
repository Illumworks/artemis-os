"""Tests for CCA9: card lifecycle -- threads, Jen's ping, the re-approval fix.

Covers the items in ``briefs/cca9-card-lifecycle.md`` "Tests" section that
don't already have a natural home in an existing file:

- The footer removal and the posted-location persistence, exercised through
  the REAL ``artemis.crisis_content.notify.post_transition_card`` pipeline
  (mirrors ``tests/test_crisis_content_routing.py``'s harness -- kept as its
  own copy here rather than a shared import, per that file's own precedent
  of not sharing fixtures across crisis-content test modules).
- Thread-reply capture + the single nudge, both directly against
  ``artemis.crisis_content.thread_notes`` and end-to-end through
  ``artemis.routes.integrations_slack_events._handle_mentionable_event`` --
  the latter proves the hook runs BEFORE the channel-allowlist gate and
  BEFORE the generic conversational loop, and that anything that is NOT a
  reply to a known card leaves existing behavior completely unchanged (the
  Lead's correction to this brief's original routing note).

The re-approval fix's own tests (fires / does-not-fire / asset-route-
mirrors, plus CCA11's approved-also-reopens / noise-guard / banner /
routes-reopen-independently cases) live in
``tests/test_crisis_content_transitions.py`` -- that module already owns
``_evaluate_route``'s test coverage, and the reopen logic lives inside
that exact function. The Jen change-request mention's tests live in
``tests/test_crisis_content_decisions.py`` -- that module already owns the
full HTTP + DB harness for the ``view_submission`` decision path the Jen
mention is threaded from. Both are required by the brief and both are
covered; see those files.
"""

from __future__ import annotations

import hashlib
import inspect
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.config import settings
from artemis.crisis_content import notify, thread_notes
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentDecision,
    CrisisContentNotification,
    CrisisContentThreadNote,
)
from artemis.crisis_content.transitions import Transition, mark_notified
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_crisis_content_lifecycle: db_url={_DB_URL!r} "
        "is not a test database."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(  # type: ignore[assignment]
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

_TABLES = (
    "crisis_content_thread_notes",
    "crisis_content_decisions",
    "crisis_content_notifications",
    "crisis_content_copy_versions",
    "crisis_content_cards",
)

_ANGELA = "angela.miata@amiralearning.com"
_HANNAH = "hannah.slater@amiralearning.com"
_JACLYN = "jaclyn.wright@amiralearning.com"
_JON = "jon.fila@amiralearning.com"
_CRISIS_CHANNEL = "C0BM9TL63TL"


@pytest.fixture(autouse=True)
def _reset_notify_caches() -> None:
    """The approver Slack-id cache is module-level (see notify.py) -- isolate tests."""
    notify.reset_notify_caches_for_tests()


@pytest.fixture(autouse=True)
def _clear_msg_dedup_cache() -> None:
    """Reset the events route's in-process message-identity dedup cache.

    Without this, a later test reusing the same ``(channel, ts)`` pair would
    be silently dropped by ``_check_and_set_msg_dedup``, same as
    ``tests/test_slack_events_channel_gate.py``'s identical fixture.
    """
    from artemis.routes.integrations_slack_events import _msg_dedup_cache

    _msg_dedup_cache.clear()


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test session against the shared module-level test engine."""
    async with AsyncSession(_test_engine, expire_on_commit=False) as session:
        await session.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()
        yield session
        await session.execute(text(f"TRUNCATE {', '.join(_TABLES)} RESTART IDENTITY CASCADE"))
        await session.commit()


def _make_card(
    *,
    header: str = "August XX, 2026 - Welcome Back blog",
    platform: str | None = "LinkedIn",
    ordinal: int = 0,
    title: str = "Welcome Back blog",
    asset_status: str | None = "Draft",
    copy_status: str | None = "Ready",
    asset_url: str | None = None,
    copy_body: str = "Default copy body.",
) -> ReviewCard:
    """Build a ``ReviewCard`` the way the real parser would, minus the HTML."""
    copy_hash = hashlib.sha256(copy_body.encode("utf-8")).hexdigest()
    return ReviewCard(
        header=header,
        date_text="August XX, 2026",
        title=title,
        platform=platform,
        asset_status=asset_status,
        copy_status=copy_status,
        asset_url=asset_url,
        copy_body=copy_body,
        identity_key=(header, platform, ordinal),
        copy_hash=copy_hash,
    )


async def _seed_card_row(session: AsyncSession, card: ReviewCard) -> int:
    """Persist the ``CrisisContentCard`` row ``post_transition_card`` expects to find.

    Mirrors ``tests/test_crisis_content_routing.py``'s helper of the same
    name, but returns the new row's id -- every test in this file needs it.
    """
    _, platform, ordinal = card.identity_key
    row = CrisisContentCard(
        identity_header=card.header,
        identity_platform=platform,
        identity_ordinal=ordinal,
        title=card.title,
        asset_status=card.asset_status,
        copy_status=card.copy_status,
        asset_url=card.asset_url,
        copy_hash=card.copy_hash,
    )
    session.add(row)
    await session.commit()
    return row.id


async def _seed_card_with_notification(
    session: AsyncSession,
    *,
    channel_id: str,
    message_ts: str,
    route: str = "copy",
    copy_body: str = "Seeded copy body.",
) -> int:
    """A card that has already been posted -- the state a thread reply arrives against."""
    card = _make_card(copy_body=copy_body)
    card_id = await _seed_card_row(session, card)
    await mark_notified(
        session,
        card_id,
        route,  # type: ignore[arg-type]
        "Ready",
        copy_hash=card.copy_hash,
        channel_id=channel_id,
        message_ts=message_ts,
    )
    await session.commit()
    return card_id


def _make_fake_notify_slack_client_cls(
    email_map: dict[str, str],
    *,
    message_response_extra: dict[str, Any] | None = None,
    dm_response_extra: dict[str, Any] | None = None,
) -> type:
    """A fake ``notify.SlackClient`` -- mirrors ``tests/test_crisis_content_routing.py``'s
    helper of the same purpose, extended to let a test control the response's
    ``channel``/``ts`` (what ``PostedCardMessage`` extracts, CCA9).
    """

    class _FakeSlackClient:
        instances: list[_FakeSlackClient] = []

        def __init__(self, token: str) -> None:
            self.token = token
            self.dm_calls: list[tuple[str, str, list[object] | None]] = []
            self.message_calls: list[tuple[str, str, list[object] | None]] = []
            _FakeSlackClient.instances.append(self)

        async def lookup_user_by_email(self, email: str) -> str | None:
            return email_map.get(email)

        async def post_dm(
            self, user: str, text: str, blocks: list[object] | None = None
        ) -> dict[str, object]:
            self.dm_calls.append((user, text, blocks))
            response: dict[str, object] = {"ok": True}
            if dm_response_extra:
                response.update(dm_response_extra)
            return response

        async def post_message(
            self,
            channel: str,
            text: str,
            thread_ts: str | None = None,
            blocks: list[object] | None = None,
        ) -> dict[str, object]:
            self.message_calls.append((channel, text, blocks))
            response: dict[str, object] = {"ok": True}
            if message_response_extra:
                response.update(message_response_extra)
            return response

    return _FakeSlackClient


async def _fake_resolve_agent_slack_config(
    session: AsyncSession, *, agent_id: str, team_id: str | None = None
) -> object:
    assert agent_id == "callie"
    return SimpleNamespace(access_token="fake-callie-token")


def _patch_notify_slack(monkeypatch: pytest.MonkeyPatch, fake_cls: type) -> None:
    monkeypatch.setattr(notify, "SlackClient", fake_cls)
    monkeypatch.setattr(notify, "_resolve_agent_slack_config", _fake_resolve_agent_slack_config)


def _fake_nudge_slack_client_cls(posted: list[tuple[str, str, str | None]]) -> type:
    """A fake ``thread_notes.SlackClient`` -- captures the nudge post only."""

    class _FakeSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def post_message(
            self,
            channel: str,
            text: str,
            thread_ts: str | None = None,
            blocks: list[object] | None = None,
        ) -> dict[str, object]:
            posted.append((channel, text, thread_ts))
            return {"ok": True}

    return _FakeSlackClient


# ─────────────────────────────────────────────────────────────────────────────
# 1. Drop the redundant footer
# ─────────────────────────────────────────────────────────────────────────────


async def test_live_copy_card_no_longer_contains_any_one_of_and_names_approvers_once(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")
    monkeypatch.setattr(settings, "crisis_content_copy_notify_channel", _CRISIS_CHANNEL)

    fake_cls = _make_fake_notify_slack_client_cls(
        {_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH", _JACLYN: "U_JACLYN"}
    )
    _patch_notify_slack(monkeypatch, fake_cls)

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, card)

    await notify.post_transition_card(db_session, transition)

    client = fake_cls.instances[0]
    _channel, msg_text, _blocks = client.message_calls[0]

    assert "Any one of" not in msg_text
    # Named exactly ONCE each -- in the opener, not a second time in a footer.
    assert msg_text.count("<@U_ANGELA>") == 1
    assert msg_text.count("<@U_HANNAH>") == 1
    assert msg_text.count("<@U_JACLYN>") == 1
    # The opener is genuinely the first line, and the doc link the last --
    # confirms there is no trailing footer line at all.
    assert msg_text.splitlines()[-1].startswith("Open the doc:")


async def test_dm_jon_override_still_renders_both_testing_footers_end_to_end(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1's other half: the ``dm_jon`` rollback footer is untouched.

    Redundant with (and a real-pipeline complement to) the pure-render
    ``test_dm_jon_testing_footers_still_render_for_both_routes`` in
    ``tests/test_crisis_content_voice.py``, which already covers this and
    is unaffected by the footer removal (that removal only touches the live
    copy path, ``_post_live_copy``).
    """
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "dm_jon")
    fake_cls = _make_fake_notify_slack_client_cls({_JON: "U_JON"})
    _patch_notify_slack(monkeypatch, fake_cls)

    copy_card = _make_card(header="Copy card", copy_status="Ready")
    copy_transition = Transition(
        card=copy_card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, copy_card)
    await notify.post_transition_card(db_session, copy_transition)

    asset_card = _make_card(
        header="Asset card",
        asset_status="Ready",
        asset_url="https://example.com/asset.png",
        copy_status="Draft",
    )
    asset_transition = Transition(
        card=asset_card, route="asset", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, asset_card)
    await notify.post_transition_card(db_session, asset_transition)

    copy_text = fake_cls.instances[0].dm_calls[0][1]
    asset_text = fake_cls.instances[1].dm_calls[0][1]
    assert notify.TESTING_LINE in copy_text
    assert notify.TESTING_LINE_ASSET in asset_text


# ─────────────────────────────────────────────────────────────────────────────
# 2. Record where each card was posted
# ─────────────────────────────────────────────────────────────────────────────


async def test_posting_a_card_persists_channel_id_message_ts_and_copy_hash(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")
    monkeypatch.setattr(settings, "crisis_content_copy_notify_channel", _CRISIS_CHANNEL)

    fake_cls = _make_fake_notify_slack_client_cls(
        {_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH", _JACLYN: "U_JACLYN"},
        message_response_extra={"channel": _CRISIS_CHANNEL, "ts": "1700000000.000100"},
    )
    _patch_notify_slack(monkeypatch, fake_cls)

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    card_id = await _seed_card_row(db_session, card)

    posted = await notify.post_transition_card(db_session, transition)
    assert posted.channel_id == _CRISIS_CHANNEL
    assert posted.message_ts == "1700000000.000100"

    # This is exactly what artemis/crisis_content/poller.py now does after a
    # successful post -- see _observe_and_notify.
    await mark_notified(
        db_session,
        card_id,
        "copy",
        "Ready",
        copy_hash=card.copy_hash,
        channel_id=posted.channel_id,
        message_ts=posted.message_ts,
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            select(CrisisContentNotification).where(CrisisContentNotification.card_id == card_id)
        )
    ).scalar_one()
    assert row.channel_id == _CRISIS_CHANNEL
    assert row.message_ts == "1700000000.000100"
    assert row.copy_hash == card.copy_hash


# ─────────────────────────────────────────────────────────────────────────────
# 3. Thread replies: capture, then nudge (direct, no event-route plumbing)
# ─────────────────────────────────────────────────────────────────────────────


async def test_thread_reply_on_known_card_creates_one_note_and_one_nudge(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    card_id = await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts="1700000000.000100"
    )

    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id=_CRISIS_CHANNEL,
        thread_ts="1700000000.000100",
        message_ts="1700000000.000200",
        slack_user_id="U_ANGELA",
        text="Looks good except the last line",
        has_files=False,
        access_token="xoxb-fake",
    )
    assert handled is True

    notes = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.card_id == card_id)
        )
    ).scalars().all()
    assert len(notes) == 1
    assert notes[0].text == "Looks good except the last line"
    assert notes[0].has_attachment is False
    assert notes[0].slack_user_id == "U_ANGELA"

    assert len(posted) == 1
    channel, nudge_text, thread_ts = posted[0]
    assert channel == _CRISIS_CHANNEL
    assert thread_ts == "1700000000.000100"
    assert "Approve" in nudge_text


async def test_second_and_third_replies_recorded_silently_no_further_nudge(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    card_id = await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts="1700000000.000100"
    )

    replies = [
        ("U_ANGELA", "First reply", "1700000000.000201"),
        ("U_HANNAH", "Second reply -- still no button click", "1700000000.000202"),
        ("U_JACLYN", "Third reply, chiming in too", "1700000000.000203"),
    ]
    for slack_user_id, reply_text, message_ts in replies:
        handled = await thread_notes.maybe_handle_thread_reply(
            db_session,
            channel_id=_CRISIS_CHANNEL,
            thread_ts="1700000000.000100",
            message_ts=message_ts,
            slack_user_id=slack_user_id,
            text=reply_text,
            has_files=False,
            access_token="xoxb-fake",
        )
        assert handled is True

    notes = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.card_id == card_id)
        )
    ).scalars().all()
    assert len(notes) == 3
    assert len(posted) == 1  # only the FIRST reply produced a nudge


async def test_reply_in_a_thread_that_is_not_a_card_is_ignored(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id="C_SOME_OTHER_CHANNEL",
        thread_ts="9999999999.000000",
        message_ts="9999999999.000100",
        slack_user_id="U_SOMEONE",
        text="totally unrelated channel chatter",
        has_files=False,
        access_token="xoxb-fake",
    )
    assert handled is False
    assert posted == []
    notes = (await db_session.execute(select(CrisisContentThreadNote))).scalars().all()
    assert notes == []


async def test_reply_with_attachment_sets_has_attachment_and_never_touches_url_private(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    card_id = await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts="1700000000.000100"
    )

    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id=_CRISIS_CHANNEL,
        thread_ts="1700000000.000100",
        message_ts="1700000000.000210",
        slack_user_id="U_ANGELA",
        text="see the attached mockup",
        has_files=True,
        access_token="xoxb-fake",
    )
    assert handled is True

    notes = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.card_id == card_id)
        )
    ).scalars().all()
    assert len(notes) == 1
    assert notes[0].has_attachment is True

    assert len(posted) == 1
    _channel, nudge_text, _thread_ts = posted[0]
    assert "image in the thread" in nudge_text

    # Never attempts to fetch url_private -- Callie has no files:read scope
    # and that call would 403. Regression guard on the CODE itself (not the
    # module's docstring, which legitimately explains this in prose) -- the
    # two functions that actually handle a reply must never reference it.
    code_source = inspect.getsource(thread_notes.handle_thread_reply) + inspect.getsource(
        thread_notes.maybe_handle_thread_reply
    )
    assert "url_private" not in code_source
    assert "files:read" not in code_source


async def test_reply_saying_approved_records_no_decision(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CRITICAL CONSTRAINT 1: the button is the only thing that decides.

    A thread reply saying "approved" must never produce a
    ``crisis_content_decisions`` row -- only a verified button click
    (``artemis.crisis_content.decisions.record_decision``) can.
    """
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    card_id = await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts="1700000000.000100"
    )

    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id=_CRISIS_CHANNEL,
        thread_ts="1700000000.000100",
        message_ts="1700000000.000220",
        slack_user_id="U_ANGELA",
        text="approved!! this is great, ship it",
        has_files=False,
        access_token="xoxb-fake",
    )
    assert handled is True

    decisions = (
        await db_session.execute(
            select(CrisisContentDecision).where(CrisisContentDecision.card_id == card_id)
        )
    ).scalars().all()
    assert decisions == []

    notes = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.card_id == card_id)
        )
    ).scalars().all()
    assert len(notes) == 1
    assert notes[0].text == "approved!! this is great, ship it"


# ─────────────────────────────────────────────────────────────────────────────
# 3b. Wiring through the Slack events route -- the hook fires BEFORE the
# channel-allowlist gate and BEFORE the generic conversational agent loop,
# and leaves everything else completely unchanged. This is the Lead's
# correction to the brief's original routing note (Callie's
# `allowed_channel_ids` deliberately excludes the crisis-content channel).
# ─────────────────────────────────────────────────────────────────────────────


def _make_callie_cfg_without_crisis_channel() -> Any:
    """Mirrors production per the Lead's correction: `listen_channel_messages`
    is True for Callie's OTHER channels, but `allowed_channel_ids` deliberately
    does NOT include the crisis-content channel -- that channel also carries
    Jon<->Jen 1:1 traffic Callie must never join uninvited. Guard 2
    (`_is_authorized_inbound`) would drop any event in this channel that
    ISN'T caught by the CCA9 hook first.
    """
    from artemis.routes.integrations_slack_events import _SlackAgentConfig

    return _SlackAgentConfig(
        agent_id="callie",
        signing_secret="secret",
        access_token="xoxb-callie-test",
        bot_user_id="BCALLIE",
        authed_user_id="U_OWNER",
        allowed_user_ids=(),
        allowed_channel_ids=("C_OTHER_MARKETING_CHANNEL",),
        listen_channel_messages=True,
        always_respond_in_channels=False,
    )


async def _run_handle_mentionable(
    db_session: AsyncSession, *, event: dict[str, Any]
) -> list[tuple[Any, ...]]:
    """Run the real ``_handle_mentionable_event`` against a REAL DB session
    (so the CCA9 hook's own query genuinely runs), mocking only Slack-config
    resolution and the audit-log dedup write -- exactly the pieces that need
    a live Slack app / a second DB table this file doesn't otherwise touch.
    """
    from artemis.routes.integrations_slack_events import _handle_mentionable_event

    payload: dict[str, Any] = {"event_id": f"Ev-{event.get('ts', '0')}", "team_id": "T_TEST"}
    agent_cfg = _make_callie_cfg_without_crisis_channel()

    bg = BackgroundTasks()
    dispatched: list[tuple[Any, ...]] = []

    def _capture_add_task(func: Any, *args: Any, **kwargs: Any) -> None:
        dispatched.append((func, args, kwargs))

    bg.add_task = _capture_add_task  # type: ignore[method-assign]

    with (
        patch(
            "artemis.routes.integrations_slack_events._resolve_agent_slack_config",
            new_callable=AsyncMock,
            return_value=agent_cfg,
        ),
        patch(
            "artemis.integrations.repository.upsert_slack_inbound",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "artemis.integrations.slack.triage.classify_mention_type",
            return_value="channel",
        ),
    ):
        await _handle_mentionable_event(
            payload,
            event,
            bg,
            db_session,
            agent_id="callie",
            inner_type=str(event.get("type", "")),
        )

    return dispatched


async def test_channel_message_not_a_reply_to_a_known_card_changes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact scenario the Lead's correction calls out: a message in the
    crisis-content channel that is NOT a reply to a known card. No note, no
    nudge, and no dispatch to the generic loop -- Guard 2 drops it exactly
    as it did before this hook existed (the channel is not in
    `allowed_channel_ids`).
    """
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    event = {
        "type": "message",
        "channel": _CRISIS_CHANNEL,
        "channel_type": "channel",
        "user": "U_SOMEONE",
        "text": "hey Jen, can you resend the latest deck?",
        "ts": "1800000000.000200",
        "thread_ts": "1800000000.000100",  # threaded, but not any known card's post
    }
    dispatched = await _run_handle_mentionable(db_session, event=event)

    assert dispatched == [], "must not reach the generic conversational loop"
    assert posted == [], "must not nudge"
    notes = (await db_session.execute(select(CrisisContentThreadNote))).scalars().all()
    assert notes == [], "must not record a note"


async def test_unthreaded_channel_chatter_changes_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same channel, no thread at all -- the plainest form of "not a reply"."""
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    event = {
        "type": "message",
        "channel": _CRISIS_CHANNEL,
        "channel_type": "channel",
        "user": "U_SOMEONE",
        "text": "morning!",
        "ts": "1800000000.000400",
    }
    dispatched = await _run_handle_mentionable(db_session, event=event)

    assert dispatched == []
    assert posted == []


async def test_cards_own_root_message_redelivered_is_not_treated_as_a_reply(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slack stamps `thread_ts` on a message equal to its OWN `ts` once it
    becomes a thread root. If that root is ever redelivered as a plain
    `message` event, `thread_ts == ts` must NOT be read as a reply to
    itself -- guards the hook's own `thread_ts != ts` check.
    """
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts="1800000000.000500"
    )

    event = {
        "type": "message",
        "channel": _CRISIS_CHANNEL,
        "channel_type": "channel",
        "user": "U_SOMEONE",
        "text": "(this is the card's own root message, redelivered)",
        "ts": "1800000000.000500",
        "thread_ts": "1800000000.000500",
    }
    dispatched = await _run_handle_mentionable(db_session, event=event)

    assert dispatched == []
    assert posted == []
    notes = (await db_session.execute(select(CrisisContentThreadNote))).scalars().all()
    assert notes == []


async def test_reply_to_known_card_is_captured_and_skips_the_generic_loop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The positive case: a genuine reply to a known card is handled by the
    hook -- captured, nudged once -- and never reaches the channel-allowlist
    gate or the generic conversational loop.
    """
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    card_id = await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts="1800000000.000100"
    )

    event = {
        "type": "message",
        "channel": _CRISIS_CHANNEL,
        "channel_type": "channel",
        "user": "U_ANGELA",
        "text": "looks good, approved!",
        "ts": "1800000000.000300",
        "thread_ts": "1800000000.000100",
    }
    dispatched = await _run_handle_mentionable(db_session, event=event)

    assert dispatched == [], "handled by the hook -- never reaches route_inbound"

    notes = (
        await db_session.execute(
            select(CrisisContentThreadNote).where(CrisisContentThreadNote.card_id == card_id)
        )
    ).scalars().all()
    assert len(notes) == 1
    assert len(posted) == 1

    # Same "approved" prose, same guarantee, at the wiring level this time.
    decisions = (
        await db_session.execute(
            select(CrisisContentDecision).where(CrisisContentDecision.card_id == card_id)
        )
    ).scalars().all()
    assert decisions == []


async def test_nudge_is_per_thread_not_per_card(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second thread on the SAME card still gets its own first-reply nudge.

    One card can be posted twice to two different places -- asset to Jon's DM,
    copy to the channel -- and the re-approval fix posts a brand-new card in a
    brand-new thread. Dedup scoped to card_id alone would swallow the nudge on
    the second thread's genuinely first reply, which is exactly the moment
    someone is most likely to assume their reply counted as an approval.

    The worker flagged this tension (the brief said "once per thread" in its
    heading but "once per card" in its mechanism) rather than guessing; the
    schema gained a thread_ts column so the dedup can be scoped correctly.
    """
    posted: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(thread_notes, "SlackClient", _fake_nudge_slack_client_cls(posted))

    thread_a = "1700000000.000100"
    thread_b = "1700000000.000900"

    card_id = await _seed_card_with_notification(
        db_session, channel_id=_CRISIS_CHANNEL, message_ts=thread_a, route="copy"
    )
    card_row = (
        await db_session.execute(
            select(CrisisContentCard).where(CrisisContentCard.id == card_id)
        )
    ).scalar_one()
    # Same card, other route, posted somewhere else -- its own thread.
    await mark_notified(
        db_session,
        card_id,
        "asset",
        "Ready",
        copy_hash=card_row.copy_hash,
        channel_id="D_JON_DM",
        message_ts=thread_b,
    )
    await db_session.commit()

    for channel_id, thread_ts, message_ts in (
        (_CRISIS_CHANNEL, thread_a, "1700000000.000101"),
        ("D_JON_DM", thread_b, "1700000000.000901"),
    ):
        handled = await thread_notes.maybe_handle_thread_reply(
            db_session,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=message_ts,
            slack_user_id="U_ANGELA",
            text="first reply in this thread",
            has_files=False,
            access_token="xoxb-fake",
        )
        assert handled is True

    assert len(posted) == 2, (
        f"expected a nudge in BOTH threads, got {len(posted)} — "
        "dedup is scoped to the card instead of the thread"
    )
    assert {p[0] for p in posted} == {_CRISIS_CHANNEL, "D_JON_DM"}

    # A SECOND reply in thread A must still stay silent.
    handled = await thread_notes.maybe_handle_thread_reply(
        db_session,
        channel_id=_CRISIS_CHANNEL,
        thread_ts=thread_a,
        message_ts="1700000000.000102",
        slack_user_id="U_HANNAH",
        text="second reply, same thread",
        has_files=False,
        access_token="xoxb-fake",
    )
    assert handled is True
    assert len(posted) == 2, "a repeat reply in an already-nudged thread must stay silent"
