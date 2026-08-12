"""Tests for CCA6: live routing (channel + real approvers, dm_jon rollback).

Covers every item in ``briefs/cca6-live-routing.md`` "Tests" section that is
about ``artemis/crisis_content/notify.py``'s own routing logic -- who a card
is addressed to, which Slack API call carries it, the testing-footer
swap, and the approver-resolution cache. The poller-level contract ("a
failed post never calls mark_notified, so the next tick retries") is
exercised end-to-end through ``run_poll_tick`` in
``tests/test_crisis_content_poller.py`` instead, since that is a property of
the poller/notify pair, not of ``notify.py`` alone.

Engine strategy mirrors ``tests/test_crisis_content_poller.py`` and
``tests/test_crisis_content_transitions.py``: a module-level NullPool engine
bound to ``ARTEMIS_TEST_DB_URL`` (falling back to ``artemis_test``), with a
hard refusal to run against anything that looks like the live database.

Slack is always a fake class swapped in for ``notify.SlackClient`` -- these
tests never touch the network. The real post is covered by the live smoke
described in the brief, run separately and pasted into the report.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.config import settings
from artemis.crisis_content import notify
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.orm import CrisisContentCard
from artemis.crisis_content.transitions import Transition
from artemis.db import attach_pgvector_codec
from artemis.integrations.slack.client import SlackAPIError

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_crisis_content_routing: db_url={_DB_URL!r} "
        "is not a test database."
    )

_test_engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
artemis.db.engine = _test_engine
artemis.db.SessionLocal = async_sessionmaker(
    _test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=True,
    autocommit=False,
)

_TABLES = (
    "crisis_content_notifications",
    "crisis_content_copy_versions",
    "crisis_content_cards",
)

# The three copy approvers per docs/crisis-content-approval-pipeline.md
# "Routing" -- deliberately NOT including Jon, who is on
# settings.crisis_content_copy_approver_emails only as an authorization
# backstop (see that field's docstring) and must never be mentioned here.
_ANGELA = "angela.miata@amiralearning.com"
_HANNAH = "hannah.slater@amiralearning.com"
_JACLYN = "jaclyn.wright@amiralearning.com"
_JON = "jon.fila@amiralearning.com"


@pytest.fixture(autouse=True)
def _reset_notify_caches() -> None:
    """The approver Slack-id cache is module-level (see notify.py) -- isolate tests."""
    notify.reset_notify_caches_for_tests()


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


async def _seed_card_row(session: AsyncSession, card: ReviewCard) -> None:
    """Persist the ``CrisisContentCard`` row ``post_transition_card`` expects to find.

    Mirrors what ``record_observation`` would have already done in a real
    poll tick -- ``post_transition_card`` looks the row up by identity to
    build the decision buttons' ``value`` and treats a miss as a bug.
    """
    _, platform, ordinal = card.identity_key
    session.add(
        CrisisContentCard(
            identity_header=card.header,
            identity_platform=platform,
            identity_ordinal=ordinal,
            title=card.title,
            asset_status=card.asset_status,
            copy_status=card.copy_status,
            asset_url=card.asset_url,
            copy_hash=card.copy_hash,
        )
    )
    await session.commit()


def _make_fake_slack_client_cls(
    email_map: dict[str, str],
    *,
    post_message_exception: BaseException | None = None,
):
    """Build a fresh fake ``SlackClient`` class recording every call it received.

    A fresh class per test (rather than one shared class) keeps
    ``instances`` from leaking between tests -- each call site swaps this in
    via ``monkeypatch.setattr(notify, "SlackClient", ...)``. Deliberately
    unannotated return type -- test modules are exempt from mandatory
    annotations (pyproject.toml's mypy override), and annotating this ``->
    type`` erases the local class's actual shape, which is exactly what
    every call site needs (``fake_cls.instances``, etc.) to type-check.
    """

    class _FakeSlackClient:
        instances: list[_FakeSlackClient] = []

        def __init__(self, token: str) -> None:
            self.token = token
            self.dm_calls: list[tuple[str, str, list[object] | None]] = []
            self.message_calls: list[tuple[str, str, list[object] | None]] = []
            self.lookup_calls: list[str] = []
            _FakeSlackClient.instances.append(self)

        async def lookup_user_by_email(self, email: str) -> str | None:
            self.lookup_calls.append(email)
            return email_map.get(email)

        async def post_dm(
            self, user: str, text: str, blocks: list[object] | None = None
        ) -> dict[str, object]:
            self.dm_calls.append((user, text, blocks))
            return {"ok": True}

        async def post_message(
            self,
            channel: str,
            text: str,
            thread_ts: str | None = None,
            blocks: list[object] | None = None,
        ) -> dict[str, object]:
            if post_message_exception is not None:
                raise post_message_exception
            self.message_calls.append((channel, text, blocks))
            return {"ok": True}

    return _FakeSlackClient


async def _fake_resolve_agent_slack_config(
    session: AsyncSession, *, agent_id: str, team_id: str | None = None
) -> object:
    assert agent_id == "callie"
    return SimpleNamespace(access_token="fake-callie-token")


def _patch_slack(monkeypatch: pytest.MonkeyPatch, fake_cls: type) -> None:
    monkeypatch.setattr(notify, "SlackClient", fake_cls)
    monkeypatch.setattr(notify, "_resolve_agent_slack_config", _fake_resolve_agent_slack_config)


# ─────────────────────────────────────────────────────────────────────────────
# copy -> channel, mentioning all three approvers, never Jon
# ─────────────────────────────────────────────────────────────────────────────


async def test_live_copy_route_posts_to_channel_with_all_three_approvers_mentioned(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")
    monkeypatch.setattr(settings, "crisis_content_copy_notify_channel", "C0BM9TL63TL")

    fake_cls = _make_fake_slack_client_cls(
        {_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH", _JACLYN: "U_JACLYN", _JON: "U_JON"}
    )
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, card)

    await notify.post_transition_card(db_session, transition)

    assert len(fake_cls.instances) == 1
    client = fake_cls.instances[0]
    assert client.dm_calls == []  # never a DM under live copy routing
    assert len(client.message_calls) == 1
    channel, msg_text, blocks = client.message_calls[0]
    assert channel == "C0BM9TL63TL"
    assert "<@U_ANGELA>" in msg_text
    assert "<@U_HANNAH>" in msg_text
    assert "<@U_JACLYN>" in msg_text
    assert "<@U_JON>" not in msg_text  # addressed to the three, never Jon
    # CCA9: the redundant "Any one of ... can approve." footer is gone --
    # the opener already names the same three approvers once.
    assert "Any one of" not in msg_text
    assert msg_text.splitlines()[-1].startswith("Open the doc:")
    assert notify.TESTING_LINE not in msg_text
    assert "Testing" not in msg_text
    assert blocks is not None
    action_ids = {
        el["action_id"]
        for block in blocks
        if block.get("type") == "actions"
        for el in block["elements"]
    }
    # CCA12: "Request changes" (which opened a modal) was replaced by
    # "Edit in doc" (crisis_content_edit_in_doc) -- see
    # tests/test_crisis_content_voice.py for that button's own coverage.
    assert action_ids == {"crisis_content_approve", "crisis_content_edit_in_doc"}


# ─────────────────────────────────────────────────────────────────────────────
# asset -> DM to Jon, not the channel
# ─────────────────────────────────────────────────────────────────────────────


async def test_live_asset_route_dms_jon_not_the_channel(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls({_JON: "U_JON"})
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(
        asset_status="Ready", asset_url="https://example.com/asset.png", copy_status="Draft"
    )
    transition = Transition(
        card=card, route="asset", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, card)

    await notify.post_transition_card(db_session, transition)

    assert len(fake_cls.instances) == 1
    client = fake_cls.instances[0]
    assert client.message_calls == []  # never a channel post
    assert len(client.dm_calls) == 1
    recipient, msg_text, blocks = client.dm_calls[0]
    assert recipient == "U_JON"
    assert "C0BM9TL63TL" not in msg_text
    assert notify.TESTING_LINE_ASSET not in msg_text
    assert "Testing" not in msg_text
    # No footer at all on a live asset card -- the last line is the doc link,
    # with no trailing blank-line-then-footer after it.
    assert msg_text.splitlines()[-1].startswith("Open the doc:")


# ─────────────────────────────────────────────────────────────────────────────
# dm_jon override -> everything to Jon, Testing footer returns
# ─────────────────────────────────────────────────────────────────────────────


async def test_dm_jon_override_sends_both_routes_to_jon_with_testing_footer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "dm_jon")

    fake_cls = _make_fake_slack_client_cls({_JON: "U_JON"})
    _patch_slack(monkeypatch, fake_cls)

    copy_card = _make_card(header="Copy card", copy_status="Ready")
    copy_transition = Transition(
        card=copy_card,
        route="copy",
        previous_status="Draft",
        new_status="Ready",
        is_new_card=False,
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
        card=asset_card,
        route="asset",
        previous_status="Draft",
        new_status="Ready",
        is_new_card=False,
    )
    await _seed_card_row(db_session, asset_card)
    await notify.post_transition_card(db_session, asset_transition)

    assert len(fake_cls.instances) == 2
    for client in fake_cls.instances:
        assert client.message_calls == []  # override never posts to the channel
        assert len(client.dm_calls) == 1
        recipient, _text, _blocks = client.dm_calls[0]
        assert recipient == "U_JON"

    copy_text = fake_cls.instances[0].dm_calls[0][1]
    asset_text = fake_cls.instances[1].dm_calls[0][1]
    assert notify.TESTING_LINE in copy_text
    assert notify.TESTING_LINE_ASSET in asset_text


# ─────────────────────────────────────────────────────────────────────────────
# One approver unresolvable -> still posts, mentions the other two, warns
# ─────────────────────────────────────────────────────────────────────────────


async def test_one_unresolvable_copy_approver_still_posts_and_logs_warning(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    # Jaclyn's email deliberately absent from the map -> unresolved.
    fake_cls = _make_fake_slack_client_cls({_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH"})
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, card)

    with caplog.at_level(logging.WARNING, logger="artemis.crisis_content.notify"):
        await notify.post_transition_card(db_session, transition)

    client = fake_cls.instances[0]
    assert len(client.message_calls) == 1
    _channel, msg_text, _blocks = client.message_calls[0]
    assert "<@U_ANGELA>" in msg_text
    assert "<@U_HANNAH>" in msg_text
    assert "jaclyn" not in msg_text.lower()

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(_JACLYN in message for message in warnings), warnings


async def test_all_copy_approvers_unresolvable_raises_and_never_posts(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not covered by name in the brief's Tests list, but the natural edge of

    "one unresolvable must not kill the notification": a card naming NOBODY
    is not a notification, so this refuses to post (raises, so
    ``mark_notified`` is never called and the next tick retries -- same
    contract as any other post failure).
    """
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls({})  # nobody resolves
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, card)

    with pytest.raises(RuntimeError):
        await notify.post_transition_card(db_session, transition)

    assert fake_cls.instances[0].message_calls == []


# ─────────────────────────────────────────────────────────────────────────────
# Callie not in the channel -> a specific, clear failure
# ─────────────────────────────────────────────────────────────────────────────


async def test_not_in_channel_failure_is_reported_specifically(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls(
        {_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH", _JACLYN: "U_JACLYN"},
        post_message_exception=SlackAPIError("chat.postMessage", "not_in_channel"),
    )
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    await _seed_card_row(db_session, card)

    with pytest.raises(RuntimeError, match="not a member"):
        await notify.post_transition_card(db_session, transition)


# ─────────────────────────────────────────────────────────────────────────────
# lookup_user_by_email, cached across calls -- not repeated per tick
# ─────────────────────────────────────────────────────────────────────────────


async def test_copy_approver_lookup_is_cached_not_repeated_per_call(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls(
        {_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH", _JACLYN: "U_JACLYN"}
    )
    _patch_slack(monkeypatch, fake_cls)

    card_one = _make_card(header="Card one", copy_status="Ready")
    card_two = _make_card(header="Card two", copy_status="Ready")
    await _seed_card_row(db_session, card_one)
    await _seed_card_row(db_session, card_two)

    # Two calls to post_transition_card == two poll ticks in the real
    # poller: each constructs its OWN SlackClient instance (a fresh Slack
    # token per tick), so a per-instance cache would not survive between
    # them -- only notify.py's module-level cache would.
    await notify.post_transition_card(
        db_session,
        Transition(
            card=card_one,
            route="copy",
            previous_status="Draft",
            new_status="Ready",
            is_new_card=False,
        ),
    )
    await notify.post_transition_card(
        db_session,
        Transition(
            card=card_two,
            route="copy",
            previous_status="Draft",
            new_status="Ready",
            is_new_card=False,
        ),
    )

    assert len(fake_cls.instances) == 2
    first_client, second_client = fake_cls.instances
    assert len(first_client.lookup_calls) == 3  # Angela, Hannah, Jaclyn
    assert second_client.lookup_calls == []  # every resolution came from cache


# ─────────────────────────────────────────────────────────────────────────────
# copy_mention_emails() itself -- Jon in, Jon filtered
# ─────────────────────────────────────────────────────────────────────────────


def test_copy_mention_emails_excludes_jons_authorization_backstop_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "crisis_content_copy_approver_emails",
        f"{_ANGELA},{_HANNAH},{_JACLYN},{_JON}",
    )

    emails = notify.copy_mention_emails()

    assert emails == [_ANGELA, _HANNAH, _JACLYN]
    assert _JON not in emails


# ─────────────────────────────────────────────────────────────────────────────
# Testing footer never appears anywhere under live routing
# ─────────────────────────────────────────────────────────────────────────────


def test_live_routing_footers_never_contain_the_testing_line() -> None:
    card = _make_card(copy_status="Ready")
    copy_transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    copy_text = notify.render_transition_message(
        copy_transition, footer="Any one of <@U1>, <@U2> or <@U3> can approve."
    )
    assert "Testing" not in copy_text
    assert notify.TESTING_LINE not in copy_text

    asset_card = _make_card(
        asset_status="Ready", asset_url="https://example.com/asset.png", copy_status="Draft"
    )
    asset_transition = Transition(
        card=asset_card,
        route="asset",
        previous_status="Draft",
        new_status="Ready",
        is_new_card=False,
    )
    asset_text = notify.render_transition_message(asset_transition, footer="")
    assert "Testing" not in asset_text
    assert notify.TESTING_LINE_ASSET not in asset_text


# ─────────────────────────────────────────────────────────────────────────────
# CCA13: transition.is_test wins over the global destination, for THIS card
# ─────────────────────────────────────────────────────────────────────────────


async def test_test_card_on_copy_route_dms_jon_never_the_channel(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A test-lane copy card must DM Jon -- never the live channel -- even
    though 'live' copy routing normally posts to the channel and mentions
    the three approvers. This is the exact failure the test lane exists to
    prevent: a duplicated test card must never reach the channel or put an
    @-mention in front of the external vendor.
    """
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls({_JON: "U_JON"})
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(header="Duplicated test card", copy_status="Ready")
    transition = Transition(
        card=card,
        route="copy",
        previous_status="Draft",
        new_status="Ready",
        is_new_card=False,
        is_test=True,
    )
    await _seed_card_row(db_session, card)

    await notify.post_transition_card(db_session, transition)

    client = fake_cls.instances[0]
    assert client.message_calls == []  # never the channel
    assert len(client.dm_calls) == 1
    recipient, msg_text, _blocks = client.dm_calls[0]
    assert recipient == "U_JON"
    assert notify.TESTING_LINE in msg_text


async def test_test_card_on_asset_route_keeps_testing_footer(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live asset-route card normally carries NO footer at all (Jon owns
    visuals, no ambiguity to flag). A test-lane asset card must still show
    the route-specific ``⚠️ Testing`` footer so Jon can tell it apart from a
    real asset waiting on him.
    """
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls({_JON: "U_JON"})
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(
        header="Duplicated test asset",
        asset_status="Ready",
        asset_url="https://example.com/asset.png",
        copy_status="Draft",
    )
    transition = Transition(
        card=card,
        route="asset",
        previous_status="Draft",
        new_status="Ready",
        is_new_card=False,
        is_test=True,
    )
    await _seed_card_row(db_session, card)

    await notify.post_transition_card(db_session, transition)

    client = fake_cls.instances[0]
    assert len(client.dm_calls) == 1
    _recipient, msg_text, _blocks = client.dm_calls[0]
    assert notify.TESTING_LINE_ASSET in msg_text


async def test_real_card_under_live_routing_has_no_testing_footer_for_contrast(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same fixture shape as the two tests above, ``is_test=False`` -- the
    direct contrast the brief's Tests section asks for: a real card under
    live routing keeps the channel destination and no footer.
    """
    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")

    fake_cls = _make_fake_slack_client_cls(
        {_ANGELA: "U_ANGELA", _HANNAH: "U_HANNAH", _JACLYN: "U_JACLYN", _JON: "U_JON"}
    )
    _patch_slack(monkeypatch, fake_cls)

    card = _make_card(header="Genuine live card", copy_status="Ready")
    transition = Transition(
        card=card,
        route="copy",
        previous_status="Draft",
        new_status="Ready",
        is_new_card=False,
        is_test=False,
    )
    await _seed_card_row(db_session, card)

    await notify.post_transition_card(db_session, transition)

    client = fake_cls.instances[0]
    assert client.dm_calls == []
    assert len(client.message_calls) == 1
    _channel, msg_text, _blocks = client.message_calls[0]
    assert notify.TESTING_LINE not in msg_text
    assert "Testing" not in msg_text
