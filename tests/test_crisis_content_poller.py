"""Tests for slice B2b: the crisis-content poller + Callie's card (CCA4).

Covers every item in ``briefs/cca4-poller-and-callie-card.md`` "Tests"
section. Slack is always mocked here -- the real post is covered by the
live smoke described in the brief, run separately and pasted into the PR/
report, not exercised by this file.

Engine strategy mirrors ``tests/test_crisis_content_transitions.py``: a
module-level NullPool engine bound to ``ARTEMIS_TEST_DB_URL`` (falling back
to ``artemis_test``), with a hard refusal to run against anything that looks
like the live database.

Network + Google-credential resolution is monkeypatched at the
``artemis.crisis_content.poller`` module boundary (``_resolve_access_token``,
``fetch_crisis_content_export_html``, ``parse_review_cards``,
``post_transition_card``) so these tests exercise the REAL persistence /
dedup / debounce / overlap-guard logic without touching Google or Slack.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.crisis_content import poller
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.orm import CrisisContentNotification
from artemis.crisis_content.parser import NoReviewCardsFoundError
from artemis.crisis_content.poller import run_poll_tick
from artemis.crisis_content.tab_resolution import CardTabInfo
from artemis.db import attach_pgvector_codec

# NOTE: no module-level `pytestmark = pytest.mark.asyncio` here (unlike
# test_crisis_content_transitions.py) -- this file mixes async DB/poller
# tests with plain sync tests of the pure render_char_count_line helper, and
# asyncio_mode = "auto" (pyproject.toml) already collects `async def test_*`
# correctly without the marker. Applying it unconditionally would tag the
# sync tests too and pytest-asyncio warns on that.

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_crisis_content_poller: db_url={_DB_URL!r} "
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
    "crisis_content_notifications",
    "crisis_content_copy_versions",
    "crisis_content_cards",
)


@pytest.fixture(autouse=True)
def _reset_failure_debounce_state() -> None:
    """The failure-debounce state is module-level (see poller.py) -- isolate tests."""
    poller.reset_poller_state_for_tests()


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


async def _default_fake_resolve_card_tab_map(
    access_token: str, document_id: str, cards: list[ReviewCard]
) -> dict[tuple[str, str | None, int], CardTabInfo]:
    """Default CCA13 tab-resolution stub: every card resolves, none are test cards.

    Every pre-CCA13 test in this file constructs cards and expects ordinary
    (non-test) persistence/notification behavior, with no opinion at all
    about tabs -- this reproduces that by resolving each card onto a generic
    real tab. Tests that DO care about tab resolution (failure, is_test)
    override this via ``monkeypatch.setattr(poller, "resolve_card_tab_map", ...)``
    after calling ``_patch_pipeline``.
    """
    return {
        card.identity_key: CardTabInfo(
            tab_id="t.cv99t981gtu6", tab_title="Content To Review", is_test=False
        )
        for card in cards
    }


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cards: list[ReviewCard] | None = None,
    parse_side_effect: BaseException | None = None,
    post_side_effect: BaseException | None = None,
) -> list[ReviewCard]:
    """Stub out Google-fetch + parse + tab-resolution + Slack-post for one
    call to run_poll_tick.

    Returns the ``cards`` list that ``parse_review_cards`` will hand back (so
    callers can assert against the same objects), unless ``parse_side_effect``
    is given, in which case parsing raises instead.
    """
    resolved_cards = cards if cards is not None else []

    async def fake_resolve_access_token(session: AsyncSession) -> str:
        return "fake-access-token"

    async def fake_fetch(*, document_id: str, access_token: str, timeout: float = 20.0) -> str:
        return "<html><!-- stubbed --></html>"

    def fake_parse(html: str) -> list[ReviewCard]:
        if parse_side_effect is not None:
            raise parse_side_effect
        return resolved_cards

    async def fake_post(session: AsyncSession, transition: object) -> None:
        if post_side_effect is not None:
            raise post_side_effect

    monkeypatch.setattr(poller, "_resolve_access_token", fake_resolve_access_token)
    monkeypatch.setattr(poller, "fetch_crisis_content_export_html", fake_fetch)
    monkeypatch.setattr(poller, "parse_review_cards", fake_parse)
    monkeypatch.setattr(poller, "resolve_card_tab_map", _default_fake_resolve_card_tab_map)
    monkeypatch.setattr(poller, "post_transition_card", fake_post)
    return resolved_cards


# ─────────────────────────────────────────────────────────────────────────────
# Happy path: one transition -> one post -> mark_notified after
# ─────────────────────────────────────────────────────────────────────────────


async def test_transition_produces_exactly_one_post_and_marks_notified_after(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def counting_post(session: AsyncSession, transition: object) -> None:
        calls.append("posted")

    card = _make_card(copy_status="Ready")
    _patch_pipeline(monkeypatch, cards=[card])
    monkeypatch.setattr(poller, "post_transition_card", counting_post)

    await run_poll_tick()

    assert calls == ["posted"]
    rows = (await db_session.execute(select(CrisisContentNotification))).scalars().all()
    assert len(rows) == 1
    assert rows[0].route == "copy"
    assert rows[0].status_value == "Ready"


# ─────────────────────────────────────────────────────────────────────────────
# Slack post failure -> no mark_notified row -> next poll retries
# ─────────────────────────────────────────────────────────────────────────────


async def test_slack_post_failure_leaves_no_notification_row_and_next_poll_retries(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    async def flaky_post(session: AsyncSession, transition: object) -> None:
        attempts.append("attempt")
        if len(attempts) == 1:
            raise RuntimeError("simulated Slack outage")

    card = _make_card(copy_status="Ready")
    _patch_pipeline(monkeypatch, cards=[card])
    monkeypatch.setattr(poller, "post_transition_card", flaky_post)

    await run_poll_tick()
    rows_after_failure = (
        await db_session.execute(select(CrisisContentNotification))
    ).scalars().all()
    assert rows_after_failure == []
    assert attempts == ["attempt"]

    # Second poll of the SAME unchanged (still "Ready") card -- must retry,
    # not treat the card as "already handled".
    await run_poll_tick()
    assert attempts == ["attempt", "attempt"]
    rows_after_retry = (
        await db_session.execute(select(CrisisContentNotification))
    ).scalars().all()
    assert len(rows_after_retry) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Two polls, unchanged doc -> exactly one post total
# ─────────────────────────────────────────────────────────────────────────────


async def test_two_polls_unchanged_doc_produce_exactly_one_post_total(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def counting_post(session: AsyncSession, transition: object) -> None:
        calls.append("posted")

    card = _make_card(copy_status="Ready")
    _patch_pipeline(monkeypatch, cards=[card])
    monkeypatch.setattr(poller, "post_transition_card", counting_post)

    await run_poll_tick()
    await run_poll_tick()

    assert calls == ["posted"]
    rows = (await db_session.execute(select(CrisisContentNotification))).scalars().all()
    assert len(rows) == 1


# ─────────────────────────────────────────────────────────────────────────────
# NoReviewCardsFoundError -> alert, no crash, poller survives
# ─────────────────────────────────────────────────────────────────────────────


async def test_no_review_cards_found_alerts_and_does_not_crash(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts: list[str] = []

    async def fake_alert(session: AsyncSession, text_: str) -> None:
        alerts.append(text_)

    monkeypatch.setattr(poller, "_alert_jon", fake_alert)
    _patch_pipeline(
        monkeypatch, parse_side_effect=NoReviewCardsFoundError("labels renamed")
    )

    # Must not raise.
    await run_poll_tick()

    assert len(alerts) == 1
    assert "abels renamed" in alerts[0] or "review cards" in alerts[0].lower()
    assert poller._failure_state.is_failing is True

    # Poller survives to the next tick -- calling again must not raise either.
    await run_poll_tick()
    assert len(alerts) == 1  # debounced, see the dedicated test below


# ─────────────────────────────────────────────────────────────────────────────
# Debounce: alert on entry + recovery only, not every tick
# ─────────────────────────────────────────────────────────────────────────────


async def test_repeated_failures_alert_on_entry_and_recovery_only(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts: list[str] = []

    async def fake_alert(session: AsyncSession, text_: str) -> None:
        alerts.append(text_)

    monkeypatch.setattr(poller, "_alert_jon", fake_alert)

    # Ticks 1-3 fail identically; tick 4 recovers (parses fine, zero cards
    # worth notifying on).
    _patch_pipeline(
        monkeypatch, parse_side_effect=NoReviewCardsFoundError("still broken")
    )
    await run_poll_tick()
    await run_poll_tick()
    await run_poll_tick()
    assert len(alerts) == 1  # entry alert only, ticks 2-3 suppressed

    _patch_pipeline(monkeypatch, cards=[])
    await run_poll_tick()
    assert len(alerts) == 2  # recovery alert
    assert "recovered" in alerts[1].lower()

    # A further healthy tick must not alert again.
    await run_poll_tick()
    assert len(alerts) == 2


# ─────────────────────────────────────────────────────────────────────────────
# t.co-aware character count
# ─────────────────────────────────────────────────────────────────────────────


def test_tco_aware_count_matches_the_brief_worked_example() -> None:
    from artemis.crisis_content.notify import render_char_count_line

    # 305 raw chars total, one 108-char URL -> 305 - 108 + 23 = 220 adjusted.
    url_prefix = "https://example.com/"
    url = url_prefix + "x" * (108 - len(url_prefix))
    assert len(url) == 108
    filler = "A" * (305 - len(url))
    copy_body = filler + url
    assert len(copy_body) == 305

    line = render_char_count_line(copy_body, "X")
    assert line == "220 chars · fits X (280)"
    assert "over" not in line.lower()


def test_x_post_genuinely_over_280_after_tco_adjustment_is_flagged() -> None:
    from artemis.crisis_content.notify import render_char_count_line

    # No URL at all -> raw length IS the adjusted length. 281 plain chars is
    # genuinely over X's 280 limit, t.co adjustment or not.
    copy_body = "A" * 281
    line = render_char_count_line(copy_body, "X")

    assert line == "281 chars · OVER X's 280 limit"


def test_combo_platform_shows_raw_count_with_no_limit_claim() -> None:
    from artemis.crisis_content.notify import render_char_count_line

    body = "Some combo-platform copy that is not especially long."
    line = render_char_count_line(body, "FB, LI, & X")

    assert str(len(body)) in line
    assert "fits" not in line.lower()
    assert "280" not in line
    assert "3,000" not in line
    assert "63,206" not in line


def test_unknown_and_none_platform_never_crash() -> None:
    from artemis.crisis_content.notify import render_char_count_line

    assert render_char_count_line("x", None) == "1 chars"
    assert render_char_count_line("hi", "All") == "2 chars (All)"
    assert render_char_count_line("hi", "TBD") == "2 chars (TBD)"


# ─────────────────────────────────────────────────────────────────────────────
# Overlap guard
# ─────────────────────────────────────────────────────────────────────────────


async def test_overlapping_tick_is_skipped_not_run_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    fetch_calls: list[int] = []

    async def slow_resolve_access_token(session: AsyncSession) -> str:
        started.set()
        await release.wait()
        return "fake-access-token"

    async def fake_fetch(*, document_id: str, access_token: str, timeout: float = 20.0) -> str:
        fetch_calls.append(1)
        return "<html></html>"

    monkeypatch.setattr(poller, "_resolve_access_token", slow_resolve_access_token)
    monkeypatch.setattr(poller, "fetch_crisis_content_export_html", fake_fetch)
    monkeypatch.setattr(poller, "parse_review_cards", lambda html: [])

    task = asyncio.create_task(run_poll_tick())
    await started.wait()
    assert poller._poll_lock.locked()

    # A second tick while the first is still inside the locked section must
    # skip immediately, not queue behind it.
    await run_poll_tick()
    assert fetch_calls == []

    release.set()
    await task
    assert fetch_calls == [1]


# ─────────────────────────────────────────────────────────────────────────────
# Destination: live routing by default; dm_jon is the override, not the norm
#
# The full routing-logic surface (copy -> channel with 3 mentions, asset ->
# Jon DM, the dm_jon override, one-approver-unresolvable, lookup caching,
# not_in_channel handling) is unit-tested against notify.py directly in
# tests/test_crisis_content_routing.py (CCA6). What belongs here, at the
# poller level, is the one property that spans both modules: a failed
# channel post must never let mark_notified run, so the next tick retries --
# see test_live_copy_channel_post_failure_leaves_no_notification_row_and_retries
# below.
# ─────────────────────────────────────────────────────────────────────────────


async def test_destination_defaults_to_live() -> None:
    from artemis.config import settings

    assert settings.crisis_content_notify_destination == "live"


async def test_live_copy_channel_post_failure_leaves_no_notification_row_and_retries(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration proof of the poller/notify contract under LIVE routing.

    Mirrors test_slack_post_failure_leaves_no_notification_row_and_next_poll_
    retries above, but exercises the REAL notify.post_transition_card (not a
    stub) so the live copy route's channel post actually runs -- only the
    Slack transport (notify.SlackClient) and Callie's token resolution
    (notify._resolve_agent_slack_config) are faked. Confirms the CCA4 retry
    contract still holds once notify.py's routing is doing real work.
    """
    from artemis.config import settings
    from artemis.crisis_content import notify
    from artemis.integrations.slack.client import SlackAPIError

    monkeypatch.setattr(settings, "crisis_content_notify_destination", "live")
    notify.reset_notify_caches_for_tests()

    card = _make_card(copy_status="Ready")

    async def fake_resolve_access_token(session: AsyncSession) -> str:
        return "fake-access-token"

    async def fake_fetch(*, document_id: str, access_token: str, timeout: float = 20.0) -> str:
        return "<html><!-- stubbed --></html>"

    def fake_parse(html: str) -> list[ReviewCard]:
        return [card]

    monkeypatch.setattr(poller, "_resolve_access_token", fake_resolve_access_token)
    monkeypatch.setattr(poller, "fetch_crisis_content_export_html", fake_fetch)
    monkeypatch.setattr(poller, "parse_review_cards", fake_parse)
    monkeypatch.setattr(poller, "resolve_card_tab_map", _default_fake_resolve_card_tab_map)
    # Deliberately NOT monkeypatching poller.post_transition_card -- this
    # test wants the real notify.post_transition_card to run.

    attempts: list[int] = []

    class _FlakyChannelSlackClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def lookup_user_by_email(self, email: str) -> str | None:
            return "U_" + email.split("@")[0].replace(".", "_").upper()

        async def post_dm(
            self, user: str, text: str, blocks: list[object] | None = None
        ) -> dict[str, object]:
            raise AssertionError("live copy routing must never DM")

        async def post_message(
            self,
            channel: str,
            text: str,
            thread_ts: str | None = None,
            blocks: list[object] | None = None,
        ) -> dict[str, object]:
            attempts.append(1)
            if len(attempts) == 1:
                raise SlackAPIError("chat.postMessage", "not_in_channel")
            return {"ok": True}

    async def fake_resolve_agent_slack_config(
        session: AsyncSession, *, agent_id: str, team_id: str | None = None
    ) -> object:
        return SimpleNamespace(access_token="fake-callie-token")

    monkeypatch.setattr(notify, "SlackClient", _FlakyChannelSlackClient)
    monkeypatch.setattr(notify, "_resolve_agent_slack_config", fake_resolve_agent_slack_config)

    await run_poll_tick()
    rows_after_failure = (
        await db_session.execute(select(CrisisContentNotification))
    ).scalars().all()
    assert rows_after_failure == []
    assert attempts == [1]

    # Same unchanged (still "Ready") card, second tick -- must retry.
    await run_poll_tick()
    assert attempts == [1, 1]
    rows_after_retry = (
        await db_session.execute(select(CrisisContentNotification))
    ).scalars().all()
    assert len(rows_after_retry) == 1


def test_testing_line_is_route_specific() -> None:
    """An asset card must not claim the copy approvers are the live audience.

    Per the routing table in docs/crisis-content-approval-pipeline.md, Jon
    approves visuals and Angela/Hannah/Jaclyn never see the asset route. A
    single shared footer would tell the reader the opposite of the design.
    """
    from artemis.crisis_content.notify import (
        TESTING_LINE,
        TESTING_LINE_ASSET,
        testing_line_for_route,
    )

    assert testing_line_for_route("copy") == TESTING_LINE
    assert testing_line_for_route("asset") == TESTING_LINE_ASSET
    assert "Angela" in TESTING_LINE
    assert "Angela" not in TESTING_LINE_ASSET
    # Both must keep the guard against mistaking testing traffic for live.
    for line in (TESTING_LINE, TESTING_LINE_ASSET):
        assert "Testing" in line
        assert "routed to you only" in line


# ─────────────────────────────────────────────────────────────────────────────
# CCA13: tab resolution -- the dangerous failure mode
# ─────────────────────────────────────────────────────────────────────────────


async def test_tab_resolution_failure_notifies_nothing_alerts_and_next_tick_retries(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The brief's "dangerous part": if the one documents.get call this tick
    fails, this tick must notify NOTHING -- no Slack post attempted, no
    ledger row, no CrisisContentCard row either (record_observation is never
    even called) -- log ERROR + alert Jon, and let the next tick retry once
    resolution succeeds. Without a resolved tab we cannot tell a test card
    from a real one, and guessing "real" risks posting Jon's duplicated test
    card to the live channel and @-mentioning the external vendor about it.
    """
    from artemis.crisis_content.tab_resolution import TabResolutionError

    alerts: list[str] = []
    posts: list[str] = []

    async def fake_alert(session: AsyncSession, text_: str) -> None:
        alerts.append(text_)

    async def counting_post(session: AsyncSession, transition: object) -> None:
        posts.append("posted")

    async def failing_resolve(
        access_token: str, document_id: str, cards: list[ReviewCard]
    ) -> dict[object, object]:
        raise TabResolutionError("documents.get returned HTTP 500")

    card = _make_card(copy_status="Ready")
    _patch_pipeline(monkeypatch, cards=[card])
    monkeypatch.setattr(poller, "resolve_card_tab_map", failing_resolve)
    monkeypatch.setattr(poller, "post_transition_card", counting_post)
    monkeypatch.setattr(poller, "_alert_jon", fake_alert)

    await run_poll_tick()

    assert posts == []  # zero notifications posted
    assert len(alerts) == 1
    assert "tab resolution" in alerts[0].lower() or "documents.get" in alerts[0].lower()
    assert poller._failure_state.is_failing is True

    rows = (await db_session.execute(select(CrisisContentNotification))).scalars().all()
    assert rows == []  # no ledger rows
    from artemis.crisis_content.orm import CrisisContentCard

    card_rows = (await db_session.execute(select(CrisisContentCard))).scalars().all()
    assert card_rows == []  # record_observation was never even reached

    # Next tick: resolution succeeds (the default _patch_pipeline stub) --
    # the SAME still-Ready card is notified normally.
    monkeypatch.setattr(poller, "resolve_card_tab_map", _default_fake_resolve_card_tab_map)
    await run_poll_tick()

    assert posts == ["posted"]
    assert len(alerts) == 2
    assert "recovered" in alerts[1].lower()
    rows_after_retry = (
        await db_session.execute(select(CrisisContentNotification))
    ).scalars().all()
    assert len(rows_after_retry) == 1


async def test_tab_resolution_call_count_is_one_per_tick_regardless_of_card_count(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration-level proof (see tests/unit_no_db/test_crisis_content_tab_resolution.py
    for the same property proven directly against resolve_card_tab_map's own
    documents.get call): the poller invokes resolve_card_tab_map exactly
    ONCE per tick no matter how many cards were parsed that tick.
    """
    calls: list[int] = []

    async def counting_resolve(
        access_token: str, document_id: str, cards: list[ReviewCard]
    ) -> dict[tuple[str, str | None, int], CardTabInfo]:
        calls.append(len(cards))
        return {
            card.identity_key: CardTabInfo(
                tab_id="t.cv99t981gtu6", tab_title="Content To Review", is_test=False
            )
            for card in cards
        }

    async def fake_post(session: AsyncSession, transition: object) -> None:
        return None

    cards = [_make_card(header=f"Card {i}", copy_status="Ready") for i in range(4)]
    _patch_pipeline(monkeypatch, cards=cards)
    monkeypatch.setattr(poller, "resolve_card_tab_map", counting_resolve)
    monkeypatch.setattr(poller, "post_transition_card", fake_post)

    await run_poll_tick()

    assert calls == [4]  # exactly one call, and it saw all 4 cards at once


async def test_tab_resolution_skipped_entirely_when_no_cards_parsed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero cards this tick -> zero documents.get calls -- no card needs a
    tab, so the extra network round trip is skipped, not merely a no-op.
    """
    calls: list[int] = []

    async def counting_resolve(
        access_token: str, document_id: str, cards: list[ReviewCard]
    ) -> dict[object, object]:
        calls.append(1)
        return {}

    _patch_pipeline(monkeypatch, cards=[])
    monkeypatch.setattr(poller, "resolve_card_tab_map", counting_resolve)

    await run_poll_tick()

    assert calls == []
