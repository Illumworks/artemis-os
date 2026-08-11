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

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db
from artemis.crisis_content.models import ReviewCard
from artemis.crisis_content.orm import (
    CrisisContentCard,
    CrisisContentCopyVersion,
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
    await mark_notified(db_session, card_row.id, "copy", "Ready")
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
    await mark_notified(db_session, old_row.id, "copy", "Ready")
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
