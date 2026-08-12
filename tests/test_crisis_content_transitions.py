"""Tests for slice B1: crisis-content transition detection + copy version log.

Covers every item in ``briefs/cca2-transition-detection.md`` "Tests"
section: card upsert + first version row, the four route/terminal/asset-url
transition rules, the notification ledger dedup, copy-version accumulation
(unchanged vs. changed), the header-rename guard (both directions), the
unknown-status WARNING, first-run behaviour, and the platform-less-collision
guard. Migration round-trip is verified separately via the shell commands in
the brief, not here.

Engine strategy mirrors ``tests/test_signal_routing_status.py``: a
module-level NullPool engine bound to ``ARTEMIS_TEST_DB_URL`` (falling back
to ``artemis_test``), with a hard refusal to run against anything that looks
like the live database.

Transaction contract under test: ``record_observation`` and
``mark_notified`` never commit (see ``artemis/crisis_content/transitions.py``
module docstring) -- this file calls ``db_session.commit()`` explicitly
after each call, exactly as a real caller (slice B2) is expected to.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.crisis_content import notify
from artemis.crisis_content.decisions import record_decision
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentCopyVersion,
    CrisisContentDecision,
)
from artemis.crisis_content.transitions import mark_notified, record_observation
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@127.0.0.1:5432/artemis_test",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD test_crisis_content_transitions: db_url={_DB_URL!r} "
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
    "crisis_content_decisions",
    "crisis_content_notifications",
    "crisis_content_copy_versions",
    "crisis_content_cards",
)


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
    copy_status: str | None = "Draft",
    asset_url: str | None = None,
    copy_body: str = "Default copy body.",
) -> ReviewCard:
    """Build a ``ReviewCard`` the way the real parser would, minus the HTML."""
    copy_hash = hashlib.sha256(copy_body.encode("utf-8")).hexdigest()
    return ReviewCard(
        header=header,
        date_text=None,
        title=title,
        platform=platform,
        asset_status=asset_status,
        copy_status=copy_status,
        asset_url=asset_url,
        copy_body=copy_body,
        identity_key=(header, platform, ordinal),
        copy_hash=copy_hash,
    )


# ─────────────────────────────────────────────────────────────────────────────
# New card / first observation
# ─────────────────────────────────────────────────────────────────────────────


async def test_new_card_at_draft_creates_row_and_version_no_transition(
    db_session: AsyncSession,
) -> None:
    card = _make_card(asset_status="Draft", copy_status="Draft")
    transitions = await record_observation(db_session, [card])
    await db_session.commit()

    assert transitions == []

    cards = (await db_session.execute(select(CrisisContentCard))).scalars().all()
    assert len(cards) == 1
    assert cards[0].identity_header == card.header
    assert cards[0].copy_hash == card.copy_hash

    versions = (await db_session.execute(select(CrisisContentCopyVersion))).scalars().all()
    assert len(versions) == 1
    assert versions[0].copy_hash == card.copy_hash


async def test_first_run_card_already_ready_emits(db_session: AsyncSession) -> None:
    """A card that is ALREADY at Ready on its very first observation still emits.

    Specified deliberately in the brief ("First-run behaviour") -- there is
    no bootstrap-suppression mode. Pin this so it can't be "fixed" later.
    """
    card = _make_card(copy_status="Ready", asset_status="Draft")
    transitions = await record_observation(db_session, [card])
    await db_session.commit()

    assert len(transitions) == 1
    transition = transitions[0]
    assert transition.route == "copy"
    assert transition.previous_status is None
    assert transition.new_status == "Ready"
    assert transition.is_new_card is True


# ─────────────────────────────────────────────────────────────────────────────
# Route rules: copy, asset (with/without URL), terminal suppression
# ─────────────────────────────────────────────────────────────────────────────


async def test_draft_to_ready_on_copy_emits_copy_transition(db_session: AsyncSession) -> None:
    draft = _make_card(copy_status="Draft", asset_status="Draft")
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready", asset_status="Draft")
    transitions = await record_observation(db_session, [ready])
    await db_session.commit()

    assert len(transitions) == 1
    assert transitions[0].route == "copy"
    assert transitions[0].previous_status == "Draft"
    assert transitions[0].new_status == "Ready"
    assert transitions[0].is_new_card is False


async def test_draft_to_ready_on_asset_with_url_emits_asset_transition(
    db_session: AsyncSession,
) -> None:
    draft = _make_card(asset_status="Draft", copy_status="Draft", asset_url=None)
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(
        asset_status="Ready",
        copy_status="Draft",
        asset_url="https://example.com/asset.png",
    )
    transitions = await record_observation(db_session, [ready])
    await db_session.commit()

    assert len(transitions) == 1
    assert transitions[0].route == "asset"
    assert transitions[0].previous_status == "Draft"
    assert transitions[0].new_status == "Ready"


async def test_draft_to_ready_on_asset_without_url_emits_nothing(
    db_session: AsyncSession,
) -> None:
    """Jon approves visuals; there is nothing to look at without an asset."""
    draft = _make_card(asset_status="Draft", copy_status="Draft", asset_url=None)
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(asset_status="Ready", copy_status="Draft", asset_url=None)
    transitions = await record_observation(db_session, [ready])
    await db_session.commit()

    assert transitions == []


async def test_terminal_statuses_never_emit(db_session: AsyncSession) -> None:
    """Ready -> Approved -> Published produces nothing past the initial Ready."""
    draft = _make_card(copy_status="Draft")
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready")
    ready_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(ready_transitions) == 1

    approved = _make_card(copy_status="Approved")
    approved_transitions = await record_observation(db_session, [approved])
    await db_session.commit()
    assert approved_transitions == []

    published = _make_card(copy_status="Published")
    published_transitions = await record_observation(db_session, [published])
    await db_session.commit()
    assert published_transitions == []


# ─────────────────────────────────────────────────────────────────────────────
# Notification ledger dedup
# ─────────────────────────────────────────────────────────────────────────────


async def test_ledger_dedup_prevents_reemit_even_after_status_changes_and_back(
    db_session: AsyncSession,
) -> None:
    """Same (card, route, status) already notified -> never re-emit.

    Exercises the ledger check independently of the previous-vs-new status
    comparison: the status leaves Ready and comes back, so a naive
    "did it change" check alone would fire again. Only the
    ``crisis_content_notifications`` row (written via ``mark_notified``,
    simulating a successful slice-B2 post) suppresses it.
    """
    draft = _make_card(copy_status="Draft")
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready")
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(first_transitions) == 1

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    back_to_draft = _make_card(copy_status="Draft")
    reset_transitions = await record_observation(db_session, [back_to_draft])
    await db_session.commit()
    assert reset_transitions == []

    ready_again = _make_card(copy_status="Ready")
    reemit_transitions = await record_observation(db_session, [ready_again])
    await db_session.commit()
    assert reemit_transitions == []


# ─────────────────────────────────────────────────────────────────────────────
# Copy version log
# ─────────────────────────────────────────────────────────────────────────────


async def test_unchanged_copy_creates_exactly_one_version_row(db_session: AsyncSession) -> None:
    card = _make_card(copy_body="Same copy every time.")
    await record_observation(db_session, [card])
    await db_session.commit()
    await record_observation(db_session, [card])
    await db_session.commit()

    versions = (await db_session.execute(select(CrisisContentCopyVersion))).scalars().all()
    assert len(versions) == 1


async def test_changed_copy_creates_two_version_rows_oldest_first(
    db_session: AsyncSession,
) -> None:
    original = _make_card(copy_body="Jen's original wording.")
    await record_observation(db_session, [original])
    await db_session.commit()

    edited = _make_card(copy_body="Our edited wording.")
    await record_observation(db_session, [edited])
    await db_session.commit()

    result = await db_session.execute(
        select(CrisisContentCopyVersion).order_by(CrisisContentCopyVersion.first_seen_at)
    )
    versions = result.scalars().all()
    assert len(versions) == 2
    assert versions[0].copy_body == "Jen's original wording."
    assert versions[1].copy_body == "Our edited wording."


# ─────────────────────────────────────────────────────────────────────────────
# Header-rename guard
# ─────────────────────────────────────────────────────────────────────────────


async def test_header_rename_with_old_card_notified_suppresses_new_card(
    db_session: AsyncSession,
) -> None:
    old_header = "August XX, 2026 - Welcome Back blog"
    copy_body = "Evergreen welcome-back copy."

    await record_observation(db_session, [_make_card(header=old_header, copy_body=copy_body, copy_status="Draft")])
    await db_session.commit()

    ready_old = _make_card(header=old_header, copy_body=copy_body, copy_status="Ready")
    old_transitions = await record_observation(db_session, [ready_old])
    await db_session.commit()
    assert len(old_transitions) == 1

    old_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, old_row.id, "copy", "Ready", copy_hash=ready_old.copy_hash)
    await db_session.commit()

    # Jen fills in the real date -- new identity_key, same copy_hash.
    renamed = _make_card(
        header="August 14, 2026 - Welcome Back blog", copy_body=copy_body, copy_status="Ready"
    )
    renamed_transitions = await record_observation(db_session, [renamed])
    await db_session.commit()

    assert renamed_transitions == []

    # Lossless: both identities persist as separate rows, no merge/overwrite.
    all_cards = (await db_session.execute(select(CrisisContentCard))).scalars().all()
    assert len(all_cards) == 2


async def test_header_rename_without_prior_notification_emits_normally(
    db_session: AsyncSession,
) -> None:
    old_header = "August XX, 2026 - Welcome Back blog"
    copy_body = "Fresh copy never actioned."

    await record_observation(
        db_session, [_make_card(header=old_header, copy_body=copy_body, copy_status="Draft")]
    )
    await db_session.commit()
    # Deliberately never call mark_notified for the old card.

    renamed = _make_card(
        header="August 14, 2026 - Welcome Back blog", copy_body=copy_body, copy_status="Ready"
    )
    transitions = await record_observation(db_session, [renamed])
    await db_session.commit()

    assert len(transitions) == 1
    assert transitions[0].route == "copy"
    assert transitions[0].is_new_card is True


# ─────────────────────────────────────────────────────────────────────────────
# Unknown status vocabulary
# ─────────────────────────────────────────────────────────────────────────────


async def test_unknown_status_logs_warning_and_emits_nothing(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    card = _make_card(copy_status="Needs legal")
    with caplog.at_level(logging.WARNING, logger="artemis.crisis_content.transitions"):
        transitions = await record_observation(db_session, [card])
    await db_session.commit()

    assert transitions == []
    assert any("Needs legal" in record.getMessage() for record in caplog.records)
    assert any(record.levelno == logging.WARNING for record in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Platform-less identity collision guard
# ─────────────────────────────────────────────────────────────────────────────


async def test_platform_less_cards_same_header_do_not_collide(db_session: AsyncSession) -> None:
    """Two distinct platform-less posts under the same header stay two rows.

    Regression guard for the NULL-comparison trap described in
    ``artemis/crisis_content/transitions.py::_resolve_card_row``: if the
    identity lookup ever regresses to a naive ``== None`` comparison, it
    would never find the existing row and would insert a duplicate on every
    poll instead of updating in place.
    """
    card_a = _make_card(header="Evergreen post", platform=None, ordinal=0, copy_body="Post A")
    card_b = _make_card(header="Evergreen post", platform=None, ordinal=1, copy_body="Post B")
    await record_observation(db_session, [card_a, card_b])
    await db_session.commit()

    rows = (await db_session.execute(select(CrisisContentCard))).scalars().all()
    assert len(rows) == 2
    ordinals = sorted(row.identity_ordinal for row in rows)
    assert ordinals == [0, 1]

    # Re-observing both should update in place, not insert two more rows.
    await record_observation(db_session, [card_a, card_b])
    await db_session.commit()
    rows_after_second_poll = (await db_session.execute(select(CrisisContentCard))).scalars().all()
    assert len(rows_after_second_poll) == 2


async def test_header_rename_suppresses_only_the_notified_route(
    db_session: AsyncSession,
) -> None:
    """A rename must not swallow a route that was never notified.

    Regression: the guard originally returned early for the whole card as
    soon as ANY route on the matching copy_hash had been notified. That lost
    an asset request permanently -- suppressed while the card was new, and
    invisible afterwards because the stored status then equals the observed
    one, so there is no transition left to detect.
    """
    old_header = "August XX, 2026 - Welcome Back blog"
    new_header = "August 18, 2026 - Welcome Back blog"
    copy_body = "Copy that survives the date fill-in unchanged."

    # Copy goes Ready under the placeholder header, and we notify on it.
    ready_old = _make_card(header=old_header, copy_body=copy_body, copy_status="Ready")
    first = await record_observation(db_session, [ready_old])
    await db_session.commit()
    assert [t.route for t in first] == ["copy"]
    old_row = (await db_session.execute(select(CrisisContentCard))).scalars().one()
    await mark_notified(db_session, old_row.id, "copy", "Ready", copy_hash=ready_old.copy_hash)
    await db_session.commit()

    # Jen fills in the real date. Same copy, new identity -- and the asset is
    # now Ready with a visual attached, which nobody has ever been asked about.
    renamed = _make_card(
        header=new_header,
        copy_body=copy_body,
        copy_status="Ready",
        asset_status="Ready",
        asset_url="https://example.com/welcome-back-visual.png",
    )
    second = await record_observation(db_session, [renamed])
    await db_session.commit()

    routes = sorted(t.route for t in second)
    assert routes == ["asset"], (
        f"expected the never-notified asset route to emit, got {routes!r} -- "
        "the copy route must stay suppressed, the asset route must not"
    )

    # And prove the loss would be permanent: a later identical poll has no
    # transition left to find, so a suppressed asset request never returns.
    third = await record_observation(db_session, [renamed])
    await db_session.commit()
    assert third == []


# ─────────────────────────────────────────────────────────────────────────────
# CCA9 -- the re-approval fix (changes_requested only)
#
# THE BUG: copy hits Ready -> card posts -> an approver requests changes ->
# Jen rewrites the copy -> nothing ever happens again. Her chip still says
# Ready (finding 5 -- we cannot write chip values), so a naive "did the
# status change" check sees no change, and even if it fired anyway the OLD
# ledger row `(card, 'copy', 'Ready')` would dedupe it. These tests are
# written FIRST per the brief's instruction ("There are required tests for
# both the fires and does-not-fire directions -- write those first") because
# getting the re-fire condition backwards means Callie re-posts the same
# card to a channel with colleagues in it every poll tick, forever.
# ─────────────────────────────────────────────────────────────────────────────

_ANGELA_SLACK_ID = "U_ANGELA"
_ANGELA_EMAIL = "angela.miata@amiralearning.com"


async def test_reapproval_after_changes_requested_and_new_copy_version_emits_once(
    db_session: AsyncSession,
) -> None:
    """Ready -> changes_requested -> a genuinely new copy version -> ONE new transition.

    This is the exact bug scenario in the brief, fixed: the chip stays
    ``Ready`` throughout (there is no Draft/Ready round-trip anywhere in
    this test), and the re-fire still happens because a revision landed
    after the decision.
    """
    draft = _make_card(copy_status="Draft", copy_body="Original wording.")
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready", copy_body="Original wording.")
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(first_transitions) == 1

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    decided_at = datetime.now(UTC) - timedelta(minutes=5)
    await record_decision(
        db_session,
        card_id=card_row.id,
        route="copy",
        decision="changes_requested",
        decided_by_slack_user_id=_ANGELA_SLACK_ID,
        decided_by_email=_ANGELA_EMAIL,
        note="tighten the second paragraph",
        decided_at=decided_at,
    )

    # Jen revises the copy. The chip Jen controls still reads "Ready" -- the
    # poller observes the SAME copy_status it already stored, only the body
    # (and therefore copy_hash) changed.
    revised = _make_card(copy_status="Ready", copy_body="Tightened wording.")
    second_transitions = await record_observation(db_session, [revised])
    await db_session.commit()

    assert len(second_transitions) == 1
    transition = second_transitions[0]
    assert transition.route == "copy"
    assert transition.new_status == "Ready"

    # Close the loop like the real poller would (post succeeded -> mark
    # notified for the NEW hash) and prove it settles rather than repeating
    # every subsequent unchanged poll -- the actual danger the brief warns
    # about ("Callie re-posts the same card every 2 minutes forever").
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=revised.copy_hash)
    await db_session.commit()

    settled_transitions = await record_observation(db_session, [revised])
    await db_session.commit()
    assert settled_transitions == []


async def test_no_new_copy_version_after_change_request_does_not_refire(
    db_session: AsyncSession,
) -> None:
    """changes_requested with NO subsequent revision -> no re-fire, ever.

    This is the guard the brief calls out by name: get this backwards and
    Callie re-pings the channel every poll tick forever.
    """
    draft = _make_card(copy_status="Draft", copy_body="Original wording.")
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready", copy_body="Original wording.")
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(first_transitions) == 1

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    await record_decision(
        db_session,
        card_id=card_row.id,
        route="copy",
        decision="changes_requested",
        decided_by_slack_user_id=_ANGELA_SLACK_ID,
        decided_by_email=_ANGELA_EMAIL,
        note="tighten the second paragraph",
    )

    # Re-poll the UNCHANGED card, repeatedly -- same copy_body, same hash, no
    # new crisis_content_copy_versions row.
    for _ in range(3):
        transitions = await record_observation(db_session, [ready])
        await db_session.commit()
        assert transitions == []


# ─────────────────────────────────────────────────────────────────────────────
# CCA11 -- reopen a post whose copy changed AFTER approval
#
# `approved` was terminal (see the now-removed
# test_approved_decision_stays_terminal_even_after_new_copy_version): once
# someone approved, nothing reopened the route. That was right when all
# editing happened before approval. It stopped being right once the vendor's
# team started editing directly in the Google Doc -- an approval that names
# specific wording, followed by someone changing that wording, is now the
# expected shape of the workflow, and an approval record that refers to text
# that no longer exists is an integrity problem for crisis communications.
#
# Written with the same "fires / does-not-fire, does-not-fire written twice"
# discipline as the CCA9 tests above, per the brief's own callout: getting
# the no-new-version case wrong means Callie re-posts an approved card into
# a channel with the external vendor in it every two minutes, forever.
# ─────────────────────────────────────────────────────────────────────────────


async def _approve_copy_ready_card(
    db_session: AsyncSession, *, copy_body: str = "Original wording."
) -> tuple[CrisisContentCard, ReviewCard, datetime]:
    """Shared setup: a copy card reaches Ready, is notified, then approved.

    Returns ``(card_row, ready_card, decided_at)`` so each test can build its
    own revision on top without repeating the draft -> ready -> notify ->
    approve boilerplate four times.
    """
    draft = _make_card(copy_status="Draft", copy_body=copy_body)
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready", copy_body=copy_body)
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(first_transitions) == 1

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    decided_at = datetime.now(UTC) - timedelta(minutes=5)
    await record_decision(
        db_session,
        card_id=card_row.id,
        route="copy",
        decision="approved",
        decided_by_slack_user_id=_ANGELA_SLACK_ID,
        decided_by_email=_ANGELA_EMAIL,
        decided_at=decided_at,
    )
    return card_row, ready, decided_at


async def test_approved_route_with_new_copy_version_refires_once(
    db_session: AsyncSession,
) -> None:
    """approved -> a genuine new copy version -> exactly ONE re-fired transition.

    Jon's decision (2026-08-12): approval is no longer terminal -- an
    approval that refers to text which no longer exists must reopen for
    approval. This replaces the old (now-wrong)
    ``test_approved_decision_stays_terminal_even_after_new_copy_version``.
    """
    card_row, _ready, _decided_at = await _approve_copy_ready_card(db_session)

    revised = _make_card(copy_status="Ready", copy_body="Edited after approval.")
    transitions = await record_observation(db_session, [revised])
    await db_session.commit()

    assert len(transitions) == 1
    transition = transitions[0]
    assert transition.route == "copy"
    assert transition.new_status == "Ready"

    # Close the loop like the real poller would, and prove it settles rather
    # than repeating on the next unchanged poll -- same discipline as the
    # CCA9 settle check above.
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=revised.copy_hash)
    await db_session.commit()
    settled = await record_observation(db_session, [revised])
    await db_session.commit()
    assert settled == []


async def test_approved_route_no_new_copy_version_does_not_refire_polled_twice(
    db_session: AsyncSession,
) -> None:
    """approved, NO subsequent revision -> no re-fire, checked on two separate polls.

    This is the guard the brief calls out by name: get this backwards and
    Callie re-posts an already-approved card into a channel with the
    external vendor in it every two minutes, forever. Polled twice in a row
    (not once) to prove the second poll doesn't fire either -- a bug that
    only shows up on the SECOND identical poll (e.g. a check that looks at
    "has this ever fired" rather than "is there a genuinely new version")
    would pass a single-poll test and still spam the channel.
    """
    _card_row, ready, _decided_at = await _approve_copy_ready_card(db_session)

    for _ in range(2):
        transitions = await record_observation(db_session, [ready])
        await db_session.commit()
        assert transitions == []


async def test_reopen_after_approval_transition_carries_banner_naming_approver_and_date(
    db_session: AsyncSession,
) -> None:
    """The re-fired card names the original approver and date -- and ONLY
    the approved-reopen case does; a changes_requested reopen (the expected
    loop) must not be mistaken for a first-time card either, but it also
    must NOT carry this banner (see the next test).
    """
    card_row, _ready, decided_at = await _approve_copy_ready_card(db_session)

    revised = _make_card(copy_status="Ready", copy_body="Edited after approval, take 2.")
    transitions = await record_observation(db_session, [revised])
    await db_session.commit()
    assert len(transitions) == 1
    transition = transitions[0]

    reopened = transition.reopened_after_approval
    assert reopened is not None
    assert reopened.approved_by == _ANGELA_EMAIL
    assert reopened.approved_at == decided_at

    rendered = notify.render_transition_message(transition, footer="")
    assert "Previously approved" in rendered
    assert _ANGELA_EMAIL in rendered
    expected_stamp = f"{decided_at.strftime('%b')} {decided_at.day}"
    assert expected_stamp in rendered
    # The banner must appear before the card's own body, not buried under it.
    assert rendered.index("Previously approved") < rendered.index(revised.copy_body)

    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=revised.copy_hash)
    await db_session.commit()


async def test_reopen_after_changes_requested_does_not_carry_banner(
    db_session: AsyncSession,
) -> None:
    """The expected changes_requested -> revision -> re-fire loop (CCA9) is
    NOT the CCA11 exception, and must not carry its banner -- collapsing the
    two would make the routine loop look like a warning, or worse, make a
    genuine re-review of approved copy look like business as usual.
    """
    draft = _make_card(copy_status="Draft", copy_body="Original wording.")
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(copy_status="Ready", copy_body="Original wording.")
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(first_transitions) == 1

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    await record_decision(
        db_session,
        card_id=card_row.id,
        route="copy",
        decision="changes_requested",
        decided_by_slack_user_id=_ANGELA_SLACK_ID,
        decided_by_email=_ANGELA_EMAIL,
        note="tighten the second paragraph",
        decided_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    revised = _make_card(copy_status="Ready", copy_body="Tightened wording.")
    transitions = await record_observation(db_session, [revised])
    await db_session.commit()
    assert len(transitions) == 1
    transition = transitions[0]

    assert transition.reopened_after_approval is None
    rendered = notify.render_transition_message(transition, footer="")
    assert "Previously approved" not in rendered
    assert "⚠️" not in rendered


async def test_copy_change_reopens_only_the_copy_route_not_asset(
    db_session: AsyncSession,
) -> None:
    """A copy-text revision reopens the approved COPY route, but the ASSET
    route -- Ready on the same card, but never given a decision of its own
    -- stays silent. Routes reopen independently: a route with no qualifying
    decision of its own never reopens, no matter what happened elsewhere on
    the same card.
    """
    draft = _make_card(
        asset_status="Draft", copy_status="Draft", asset_url=None, copy_body="V1 copy."
    )
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(
        asset_status="Ready",
        copy_status="Ready",
        asset_url="https://example.com/asset.png",
        copy_body="V1 copy.",
    )
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    routes = sorted(t.route for t in first_transitions)
    assert routes == ["asset", "copy"]

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=ready.copy_hash)
    await mark_notified(db_session, card_row.id, "asset", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    # Only the COPY route is ever decided -- the asset was never approved or
    # sent back, e.g. the visual is still awaiting Jon's attention elsewhere.
    await record_decision(
        db_session,
        card_id=card_row.id,
        route="copy",
        decision="approved",
        decided_by_slack_user_id=_ANGELA_SLACK_ID,
        decided_by_email=_ANGELA_EMAIL,
        decided_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    revised = _make_card(
        asset_status="Ready",
        copy_status="Ready",
        asset_url="https://example.com/asset.png",
        copy_body="V2 copy.",
    )
    second_transitions = await record_observation(db_session, [revised])
    await db_session.commit()

    assert [t.route for t in second_transitions] == ["copy"], (
        f"expected only the copy route to reopen, got {[t.route for t in second_transitions]!r} "
        "-- the asset route has no decision of its own and must not reopen"
    )


async def test_two_distinct_copy_versions_required_for_two_refires_across_several_polls(
    db_session: AsyncSession,
) -> None:
    """One revision produces exactly one re-fire -- polling the SAME revision
    repeatedly must not multiply it, and a SECOND genuinely distinct revision
    must still get its own re-fire.
    """
    card_row, _ready, _decided_at = await _approve_copy_ready_card(db_session)

    revision_one = _make_card(copy_status="Ready", copy_body="Revision one.")
    first = await record_observation(db_session, [revision_one])
    await db_session.commit()
    assert len(first) == 1
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=revision_one.copy_hash)
    await db_session.commit()

    for _ in range(3):
        repeat = await record_observation(db_session, [revision_one])
        await db_session.commit()
        assert repeat == [], "the same revision must not keep re-firing across repeated polls"

    revision_two = _make_card(copy_status="Ready", copy_body="Revision two.")
    second = await record_observation(db_session, [revision_two])
    await db_session.commit()
    assert len(second) == 1, "a second, genuinely distinct revision must still get its own re-fire"
    await mark_notified(db_session, card_row.id, "copy", "Ready", copy_hash=revision_two.copy_hash)
    await db_session.commit()

    for _ in range(3):
        repeat = await record_observation(db_session, [revision_two])
        await db_session.commit()
        assert repeat == []


async def test_reopen_after_approval_writes_no_decision_and_prior_decisions_survive(
    db_session: AsyncSession,
) -> None:
    """The reopen itself is read-only w.r.t. decisions: no new
    ``crisis_content_decisions`` row is written by ``record_observation``,
    and the original approval row survives untouched (CLAUDE.md rule 3,
    append-only).
    """
    card_row, _ready, decided_at = await _approve_copy_ready_card(db_session)

    before = (
        (await db_session.execute(select(CrisisContentDecision))).scalars().all()
    )
    assert len(before) == 1
    assert before[0].decision == "approved"
    assert before[0].decided_at == decided_at

    revised = _make_card(copy_status="Ready", copy_body="Edited after approval.")
    transitions = await record_observation(db_session, [revised])
    await db_session.commit()
    assert len(transitions) == 1

    after = (
        (await db_session.execute(select(CrisisContentDecision))).scalars().all()
    )
    assert len(after) == 1, "the reopen must not write a decision of its own"
    assert after[0].id == before[0].id
    assert after[0].decision == "approved"
    assert after[0].decided_at == decided_at
    assert after[0].card_id == card_row.id


async def test_asset_route_reapproval_mirrors_copy_route_rule(
    db_session: AsyncSession,
) -> None:
    """The asset route re-fires under the identical rule -- mirrored, not special-cased.

    There is no asset-specific version log (brief: "no new column is
    needed"), so the signal that "this post was touched since the
    decision" is the SAME ``crisis_content_copy_versions`` log, keyed only
    by ``card_id``. A revision to the COPY text still reopens the ASSET
    route's ``changes_requested`` decision.
    """
    draft = _make_card(
        asset_status="Draft", copy_status="Draft", asset_url=None, copy_body="V1 copy."
    )
    await record_observation(db_session, [draft])
    await db_session.commit()

    ready = _make_card(
        asset_status="Ready",
        copy_status="Draft",
        asset_url="https://example.com/asset.png",
        copy_body="V1 copy.",
    )
    first_transitions = await record_observation(db_session, [ready])
    await db_session.commit()
    assert len(first_transitions) == 1
    assert first_transitions[0].route == "asset"

    card_row = (await db_session.execute(select(CrisisContentCard))).scalar_one()
    await mark_notified(db_session, card_row.id, "asset", "Ready", copy_hash=ready.copy_hash)
    await db_session.commit()

    decided_at = datetime.now(UTC) - timedelta(minutes=5)
    await record_decision(
        db_session,
        card_id=card_row.id,
        route="asset",
        decision="changes_requested",
        decided_by_slack_user_id="U_JON",
        decided_by_email="jon.fila@amiralearning.com",
        note="crop the visual tighter",
        decided_at=decided_at,
    )

    # The VISUAL didn't change (same asset_url, still Ready) -- only the
    # copy text did, which is enough: a new crisis_content_copy_versions row
    # for this card, after the asset decision's decided_at.
    revised = _make_card(
        asset_status="Ready",
        copy_status="Draft",
        asset_url="https://example.com/asset.png",
        copy_body="V2 copy.",
    )
    second_transitions = await record_observation(db_session, [revised])
    await db_session.commit()

    assert len(second_transitions) == 1
    assert second_transitions[0].route == "asset"
    assert second_transitions[0].new_status == "Ready"
