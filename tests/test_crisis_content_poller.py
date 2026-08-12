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


def _patch_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    cards: list[ReviewCard] | None = None,
    parse_side_effect: BaseException | None = None,
    post_side_effect: BaseException | None = None,
) -> list[ReviewCard]:
    """Stub out Google-fetch + parse + Slack-post for one call to run_poll_tick.

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
# Destination: DM-to-Jon only, never the channel
# ─────────────────────────────────────────────────────────────────────────────


async def test_destination_defaults_to_dm_jon() -> None:
    from artemis.config import settings

    assert settings.crisis_content_notify_destination == "dm_jon"


async def test_post_transition_card_never_posts_to_a_channel(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.crisis_content import notify
    from artemis.crisis_content.orm import CrisisContentCard
    from artemis.crisis_content.transitions import Transition

    class _FakeSlackClient:
        instances: list[_FakeSlackClient] = []

        def __init__(self, token: str) -> None:
            self.token = token
            self.dm_calls: list[tuple[str, str, list[object] | None]] = []
            self.message_calls: list[tuple[str, str]] = []
            _FakeSlackClient.instances.append(self)

        async def lookup_user_by_email(self, email: str) -> str | None:
            assert email == "jon.fila@amiralearning.com"
            return "U_JON_FAKE"

        async def post_dm(
            self, user: str, text: str, blocks: list[object] | None = None
        ) -> dict[str, object]:
            self.dm_calls.append((user, text, blocks))
            return {"ok": True}

        async def post_message(
            self, channel: str, text: str, **kwargs: object
        ) -> dict[str, object]:
            self.message_calls.append((channel, text))
            return {"ok": True}

    async def fake_resolve_agent_slack_config(
        session: AsyncSession, *, agent_id: str, team_id: str | None = None
    ) -> object:
        assert agent_id == "callie"
        return SimpleNamespace(access_token="fake-callie-token")

    monkeypatch.setattr(notify, "_resolve_agent_slack_config", fake_resolve_agent_slack_config)
    monkeypatch.setattr(notify, "SlackClient", _FakeSlackClient)
    _FakeSlackClient.instances.clear()

    card = _make_card(copy_status="Ready")
    transition = Transition(
        card=card, route="copy", previous_status="Draft", new_status="Ready", is_new_card=False
    )
    # post_transition_card (CCA5) resolves the persisted card row to embed
    # `card_id` in the decision buttons' `value` -- seed the row a real
    # caller (poller.py, via record_observation) would already have created.
    db_session.add(
        CrisisContentCard(
            identity_header=card.header,
            identity_platform=card.platform,
            identity_ordinal=0,
            title=card.title,
            asset_status=card.asset_status,
            copy_status=card.copy_status,
            asset_url=card.asset_url,
            copy_hash=card.copy_hash,
        )
    )
    await db_session.commit()

    await notify.post_transition_card(db_session, transition)

    assert len(_FakeSlackClient.instances) == 1
    client = _FakeSlackClient.instances[0]
    assert client.message_calls == []  # never a channel post
    assert len(client.dm_calls) == 1
    recipient, text, blocks = client.dm_calls[0]
    assert recipient == "U_JON_FAKE"
    assert recipient != "C0BM9TL63TL"
    assert "C0BM9TL63TL" not in text
    assert blocks is not None
    action_ids = {
        el["action_id"]
        for block in blocks
        if block.get("type") == "actions"
        for el in block["elements"]
    }
    assert action_ids == {"crisis_content_approve", "crisis_content_request_changes"}
    assert notify.TESTING_LINE in text
    assert "action_id" not in text  # no interactive buttons


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
