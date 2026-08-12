"""CCA14 -- harvest approved crisis-content copy into Writing Studio.

Covers every item in ``briefs/cca14-harvest-approved-copy.md`` "Tests"
section. DB-backed tests exercise ``artemis.crisis_content.harvest.harvest_decision``
directly (not through the Slack interactivity route -- that plumbing is
already covered by ``tests/test_crisis_content_decisions.py``, and this
slice's own hook into it is a single line in ``slack_actions.py``).

Fixture strategy mirrors ``tests/test_crisis_content_writeback.py``: a
per-test engine bound to ``ARTEMIS_TEST_DB_URL`` (or ``ARTEMIS_DB_URL``),
with a hard refusal to run against anything that isn't a test database, and
a TRUNCATE before each test for isolation.

**Seeding the shape production actually has** (CLAUDE.md, "Passing tests are
not evidence the thing works"): ``_seed_writing_studio_baseline`` recreates
the exact state ``docs/crisis-content-approval-pipeline.md`` records as read
2026-08-11 -- one ``Amira Marketing Voice`` profile, a handful of
``writing_rules``, a batch of ``writing_training_candidates`` in
Angela's queue, and 7 ``writing_examples`` rows with ``channel IS NULL``.
Every "untouched" assertion below is against THIS seeded baseline, not an
empty table -- an empty table can't demonstrate that harvesting one profile
leaves a DIFFERENT profile's rules and candidates alone.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import NullPool, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.crisis_content import harvest
from artemis.crisis_content.decisions import record_decision
from artemis.crisis_content.orm import CrisisContentCard, CrisisContentCopyVersion, CrisisContentDecision
from artemis.db import attach_pgvector_codec

# WritingTrainingCandidate.draft_id FKs to campaign_deliverables.id. Nothing
# else in this file imports that model, but SQLAlchemy needs it registered
# in Base.metadata to resolve the FK when flushing a WritingTrainingCandidate
# -- without this import, seeding one raises NoReferencedTableError.
from artemis.marketing.models import CampaignDeliverable  # noqa: F401
from artemis.writing_rules.models import (
    WritingExample,
    WritingProfile,
    WritingRule,
    WritingTrainingCandidate,
)

# NOTE: no module-level pytestmark (mirrors test_crisis_content_writeback.py)
# -- this file mixes async DB tests with plain sync tests of
# resolve_channels(), and asyncio_mode = "auto" (pyproject.toml) already
# collects `async def test_*` correctly without the marker.

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL") or os.environ.get("ARTEMIS_DB_URL", "")
if "artemis_test" not in _db_url:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_db_url!r} is not a test database. "
        "TRUNCATE on the live DB would destroy production data."
    )

_TRUNCATE = text(
    "TRUNCATE crisis_content_decisions, crisis_content_notifications, "
    "crisis_content_copy_versions, crisis_content_cards, "
    "writing_training_candidates, claims, templates, writing_sources, "
    "writing_folders, writing_examples, writing_rules, writing_profiles "
    "RESTART IDENTITY CASCADE"
)

_MARKETING_PROFILE_NAME = "Amira Marketing Voice"


@pytest.fixture(autouse=True)
def _harvest_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the kill switch on for this suite, independent of its shipped default.

    Mirrors ``test_crisis_content_writeback.py``'s ``_writeback_enabled``
    fixture and its reasoning: a test should never be the reason a settings
    default is pinned one way or the other.
    """
    from artemis.config import settings

    monkeypatch.setattr(settings, "crisis_content_harvest_enabled", True)


@pytest.fixture(autouse=True)
def _capture_alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture ``harvest._alert_jon`` calls instead of hitting Slack.

    Every real call would resolve a Slack token and fail closed with a
    logged warning anyway (no integration is seeded in this suite), but
    asserting "an alert was raised" against a captured call is more direct
    and less brittle than asserting against a log line.
    """
    calls: list[str] = []

    async def fake_alert(_session: AsyncSession, text_: str) -> None:
        calls.append(text_)

    monkeypatch.setattr(harvest, "_alert_jon", fake_alert)
    return calls


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


# ── helpers ──────────────────────────────────────────────────────────────


async def _seed_writing_studio_baseline(db_session: AsyncSession) -> int:
    """Recreate the production shape read 2026-08-11. Returns the marketing
    profile's id, for later untouched-assertions.

    Deliberately does NOT wrap this in ``async with db_session.begin():`` --
    the fixture session uses SQLAlchemy's autobegin behaviour (a transaction
    may already be implicitly open from an earlier plain ``execute``/``get``
    in the same test), and calling ``.begin()`` explicitly when one is
    already open raises ``InvalidRequestError``. A plain ``commit()`` at the
    end closes whichever transaction -- autobegun or not -- is active.
    """
    marketing_profile = WritingProfile(
        name=_MARKETING_PROFILE_NAME,
        description="Shared writing profile for Angela and the marketing team",
        status="active",
    )
    db_session.add(marketing_profile)
    await db_session.flush()

    for i in range(3):
        db_session.add(
            WritingRule(
                profile_id=marketing_profile.id,
                rule_type="voice",
                title=f"Standing rule {i}",
                body=f"Rule body {i}",
                status="active",
            )
        )

    for i in range(5):
        db_session.add(
            WritingTrainingCandidate(
                profile_id=marketing_profile.id,
                candidate_type="rule",
                proposed_text=f"Angela's proposed rule {i}",
                status="proposed",
            )
        )

    for i in range(6):
        db_session.add(
            WritingExample(
                profile_id=marketing_profile.id,
                title=f"Reference material {i}",
                body=f"Reference body {i}",
                example_type="reference",
                channel=None,
            )
        )
    db_session.add(
        WritingExample(
            profile_id=marketing_profile.id,
            title="Proof pack template",
            body="Template body",
            example_type="template",
            channel=None,
        )
    )
    await db_session.commit()
    return marketing_profile.id


async def _seed_card(
    db_session: AsyncSession,
    *,
    header: str = "August 5 - Back to School Push",
    title: str | None = "Back to School Push",
    platform: str | None = "LinkedIn",
    ordinal: int = 0,
    copy_body: str = "Default approved copy body.",
    is_test: bool = False,
) -> int:
    """Insert a card AND its matching copy-version row (the invariant
    ``artemis.crisis_content.transitions`` maintains in production -- every
    observed ``(card, copy_hash)`` pair gets a version row the same tick the
    card row is upserted).
    """
    copy_hash = hashlib.sha256(copy_body.encode("utf-8")).hexdigest()
    row = CrisisContentCard(
        identity_header=header,
        identity_platform=platform,
        identity_ordinal=ordinal,
        title=title,
        asset_status="Draft",
        copy_status="Ready",
        asset_url=None,
        copy_hash=copy_hash,
        is_test=is_test,
    )
    db_session.add(row)
    await db_session.flush()
    db_session.add(
        CrisisContentCopyVersion(
            card_id=row.id,
            copy_hash=copy_hash,
            copy_body=copy_body,
            first_seen_at=datetime.now(UTC),
        )
    )
    await db_session.commit()
    return row.id


async def _seed_decision(
    db_session: AsyncSession,
    *,
    card_id: int,
    route: str = "copy",
    decision: str = "approved",
    decided_by_email: str | None = "angela.miata@amiralearning.com",
    decided_by_slack_user_id: str = "U_ANGELA",
) -> CrisisContentDecision:
    return await record_decision(
        db_session,
        card_id=card_id,
        route=route,  # type: ignore[arg-type]
        decision=decision,  # type: ignore[arg-type]
        decided_by_slack_user_id=decided_by_slack_user_id,
        decided_by_email=decided_by_email,
    )


async def _examples(db_session: AsyncSession) -> list[WritingExample]:
    result = await db_session.execute(select(WritingExample).order_by(WritingExample.id))
    return list(result.scalars())


async def _count(db_session: AsyncSession, model: Any) -> int:
    result = await db_session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


# ── pure resolve_channels() tests -- no DB ──────────────────────────────────


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("Facebook", ("facebook",)),
        ("FB", ("facebook",)),
        ("Instagram", ("instagram",)),
        ("IG", ("instagram",)),
        ("LinkedIn", ("linkedin",)),
        ("LI", ("linkedin",)),
        ("X", ("x",)),
        ("Twitter", ("x",)),
        ("TWITTER(X)", ("x",)),
        ("FB/IG", ("facebook", "instagram")),
        ("Facebook/LinkedIn", ("facebook", "linkedin")),
        ("FB, LI, & X", ("facebook", "linkedin", "x")),
        ("Facebook, IG, X", ("facebook", "instagram", "x")),
        ("All", harvest.CANONICAL_CHANNELS),
        ("all", harvest.CANONICAL_CHANNELS),
    ],
)
def test_resolve_channels_recognizes_documented_values(
    platform: str, expected: tuple[str, ...]
) -> None:
    resolution = harvest.resolve_channels(platform)
    assert resolution.status == "resolved"
    assert resolution.channels == tuple(expected)


@pytest.mark.parametrize("platform", ["TBD", "tbd", "  TBD  ", "", "   ", None])
def test_resolve_channels_tbd_and_blank_are_no_platform(platform: str | None) -> None:
    resolution = harvest.resolve_channels(platform)
    assert resolution.status == "no_platform"
    assert resolution.channels == ()


def test_resolve_channels_unrecognized_value() -> None:
    resolution = harvest.resolve_channels("Threads")
    assert resolution.status == "unrecognized"
    assert resolution.channels == ()


# ── DB-backed harvest_decision() tests ──────────────────────────────────────


async def test_approved_single_platform_harvests_one_row(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Approved LinkedIn copy.")
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "harvested"
    assert outcome.rows_written == 1
    assert outcome.channels == ("linkedin",)

    examples = await _examples(db_session)
    harvested = [e for e in examples if e.example_type == "approved_post"]
    assert len(harvested) == 1
    row = harvested[0]
    assert row.channel == "linkedin"
    assert row.body == "Approved LinkedIn copy."
    assert row.quality == "unrated"

    profile = await db_session.get(WritingProfile, row.profile_id)
    assert profile is not None
    assert profile.name == harvest.SOCIAL_PROFILE_NAME


async def test_multi_platform_combo_fans_out_three_rows(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(
        db_session, platform="FB, LI, & X", copy_body="One post, three platforms."
    )
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "harvested"
    assert outcome.rows_written == 3
    assert set(outcome.channels) == {"facebook", "linkedin", "x"}

    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert len(harvested) == 3
    assert {e.channel for e in harvested} == {"facebook", "linkedin", "x"}
    assert all(e.body == "One post, three platforms." for e in harvested)
    # Same copy_hash across all three fan-out rows -- see the module
    # docstring's "Idempotency" section.
    assert len({e.copy_hash for e in harvested}) == 1


async def test_tbd_harvests_nothing_no_crash(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="TBD", copy_body="Not yet assigned.")
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "no_platform"
    assert outcome.rows_written == 0
    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert harvested == []


async def test_unrecognized_platform_alerts_and_never_stores_literal_channel(
    db_session: AsyncSession, _capture_alerts: list[str]
) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="Threads", copy_body="New platform, who dis.")
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "unrecognized_platform"
    assert outcome.rows_written == 0
    assert len(_capture_alerts) == 1
    assert "Threads" in _capture_alerts[0]

    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert harvested == []
    assert all(e.channel != "Threads" for e in await _examples(db_session))


async def test_changes_requested_does_not_harvest(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Not approved yet.")
    decision = await _seed_decision(db_session, card_id=card_id, decision="changes_requested")

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "not_approved"
    assert outcome.rows_written == 0
    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert harvested == []


async def test_asset_route_approval_does_not_harvest(db_session: AsyncSession) -> None:
    """Correctness fix found during implementation, not spelled out verbatim in
    the brief's Tests list: only a COPY-route approval harvests text. See
    ``harvest_decision``'s docstring for why an asset-route approval must not.
    """
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Graphic approved, not copy.")
    decision = await _seed_decision(
        db_session,
        card_id=card_id,
        route="asset",
        decided_by_email="jon.fila@amiralearning.com",
        decided_by_slack_user_id="U_JON",
    )

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "wrong_route"
    assert outcome.rows_written == 0
    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert harvested == []


async def test_test_card_never_harvests(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(
        db_session, platform="LinkedIn", copy_body="A test lane post.", is_test=True
    )
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await harvest.harvest_decision(db_session, decision)

    # Assert explicitly, per the brief.
    assert outcome.status == "test_card"
    assert outcome.rows_written == 0
    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert harvested == [], "a test card must never produce a writing_examples row"


async def test_reharvesting_same_decision_does_not_duplicate(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Idempotency check.")
    decision = await _seed_decision(db_session, card_id=card_id)

    first = await harvest.harvest_decision(db_session, decision)
    second = await harvest.harvest_decision(db_session, decision)

    assert first.rows_written == 1
    assert second.status == "harvested"
    assert second.rows_written == 0, "a re-run must write zero NEW rows"

    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert len(harvested) == 1


async def test_social_profile_created_once_and_reused(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_1 = await _seed_card(
        db_session, header="August 5 - Post One", platform="LinkedIn", copy_body="Post one."
    )
    card_2 = await _seed_card(
        db_session, header="August 12 - Post Two", platform="Facebook", copy_body="Post two."
    )
    decision_1 = await _seed_decision(db_session, card_id=card_1)
    decision_2 = await _seed_decision(db_session, card_id=card_2)

    await harvest.harvest_decision(db_session, decision_1)
    await harvest.harvest_decision(db_session, decision_2)

    result = await db_session.execute(
        select(WritingProfile).where(WritingProfile.name == harvest.SOCIAL_PROFILE_NAME)
    )
    profiles = list(result.scalars())
    assert len(profiles) == 1


async def test_existing_profile_rules_and_candidates_are_untouched(
    db_session: AsyncSession,
) -> None:
    marketing_profile_id = await _seed_writing_studio_baseline(db_session)

    rules_before = await _count(db_session, WritingRule)
    candidates_before = await _count(db_session, WritingTrainingCandidate)
    marketing_profile_before = await db_session.get(WritingProfile, marketing_profile_id)
    assert marketing_profile_before is not None
    name_before = marketing_profile_before.name

    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Untouched-baseline check.")
    decision = await _seed_decision(db_session, card_id=card_id)
    await harvest.harvest_decision(db_session, decision)

    rules_after = await _count(db_session, WritingRule)
    candidates_after = await _count(db_session, WritingTrainingCandidate)
    marketing_profile_after = await db_session.get(WritingProfile, marketing_profile_id)
    assert marketing_profile_after is not None

    assert rules_after == rules_before
    assert candidates_after == candidates_before
    assert marketing_profile_after.name == name_before == _MARKETING_PROFILE_NAME

    # And the 7 pre-existing reference/template rows are still exactly 7,
    # untouched, still channel-less.
    legacy = [
        e
        for e in await _examples(db_session)
        if e.example_type in ("reference", "template")
    ]
    assert len(legacy) == 7
    assert all(e.channel is None for e in legacy)


async def test_quality_defaults_to_unrated(db_session: AsyncSession) -> None:
    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Quality default check.")
    decision = await _seed_decision(db_session, card_id=card_id)

    await harvest.harvest_decision(db_session, decision)

    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert len(harvested) == 1
    assert harvested[0].quality == "unrated"


async def test_harvest_reads_current_copy_not_an_earlier_snapshot(
    db_session: AsyncSession,
) -> None:
    """The brief's 'Subtlety that will bite if ignored': capture the copy AS
    IT READS AT HARVEST TIME, by re-reading the card's current state -- not
    whatever text existed earlier (e.g. when a notification first fired).

    Simulated here as: the card is first observed with one copy_hash/body,
    then revised (a second, later copy-version row + the card's own
    copy_hash pointer both move to the new text) -- exactly what
    ``artemis.crisis_content.transitions`` does on a later poll tick. The
    decision is recorded against the card as it stands NOW (post-revision);
    harvesting must read the CURRENT text, never the original.
    """
    original_body = "Draft wording nobody signed off on."
    revised_body = "Final wording that was actually approved."
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body=original_body)
    await _seed_writing_studio_baseline(db_session)

    revised_hash = hashlib.sha256(revised_body.encode("utf-8")).hexdigest()
    row = (
        await db_session.execute(select(CrisisContentCard).where(CrisisContentCard.id == card_id))
    ).scalar_one()
    row.copy_hash = revised_hash
    db_session.add(
        CrisisContentCopyVersion(
            card_id=card_id,
            copy_hash=revised_hash,
            copy_body=revised_body,
            first_seen_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    decision = await _seed_decision(db_session, card_id=card_id)
    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "harvested"
    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert len(harvested) == 1
    assert harvested[0].body == revised_body
    assert harvested[0].body != original_body
    assert harvested[0].copy_hash == revised_hash


async def test_harvest_disabled_via_settings_does_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from artemis.config import settings

    monkeypatch.setattr(settings, "crisis_content_harvest_enabled", False)

    await _seed_writing_studio_baseline(db_session)
    card_id = await _seed_card(db_session, platform="LinkedIn", copy_body="Kill switch check.")
    decision = await _seed_decision(db_session, card_id=card_id)

    outcome = await harvest.harvest_decision(db_session, decision)

    assert outcome.status == "disabled"
    harvested = [e for e in await _examples(db_session) if e.example_type == "approved_post"]
    assert harvested == []
