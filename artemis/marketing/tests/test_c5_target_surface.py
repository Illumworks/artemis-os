"""Josh's filtered view (owner decision 2026-08-25, option 2).

Targets, plus statewide signals in states where he has at least one target.
Everything else held back — and anything we merely failed to match is held
SEPARATELY, never mixed in with the deliberate exclusions.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from artemis.marketing.targets.ingest import import_target_accounts
from artemis.marketing.targets.surface import select_for_targets

HEADER = (
    "Sales\tBilling State/Province\tAccount Name\tDistrict Marketing Tier\t"
    "Enrollment in District\tIs Customer\tIs Parent Account\tAmira Channel Partner"
)


async def _seed_targets(session, *rows: str) -> None:
    await import_target_accounts(session, "\n".join([HEADER, *rows]) + "\n")


async def _signal(session, *, headline: str, state: str, district: str | None) -> int:
    """Insert a signal shaped like production's.

    `campaign_family` is NOT NULL with no default and 'general_growth' is what
    2,515 of the live rows carry -- omitting it (as the first draft of this file
    did) fails at insert, which is the cheap version of the CLAUDE.md lesson
    about seeding the shape production actually has.
    """
    result = await session.execute(
        text(
            """
            INSERT INTO signal_queue
                (headline, summary, source_url, source_type, urgency_tier,
                 campaign_family, district_id, state, signal_status,
                 created_at, updated_at)
            VALUES (:h, '', '', 'test', 'tier_3',
                    'general_growth', :d, :s, 'qualified', now(), now())
            RETURNING id
            """
        ),
        {"h": headline, "d": district, "s": state},
    )
    await session.flush()
    return int(result.scalar_one())


@pytest.mark.asyncio
async def test_a_target_account_signal_is_surfaced_with_its_tier(db_session) -> None:
    await _seed_targets(
        db_session, "Natasha\tTX\tDallas Independent School District\tD1\t137899\t0\t1\t"
    )
    await _signal(
        db_session,
        headline="Dallas ISD names new chief academic officer",
        state="TX",
        district="Dallas Independent School District",
    )

    out = await select_for_targets(db_session, days=1)

    assert len(out.targets) == 1
    assert out.targets[0].account_name == "Dallas Independent School District"
    assert out.targets[0].marketing_tier == "D1"


@pytest.mark.asyncio
async def test_a_non_target_district_is_held_back(db_session) -> None:
    """THE complaint: Fort Worth is not on his list, so it must not reach him."""
    await _seed_targets(
        db_session, "Natasha\tTX\tDallas Independent School District\tD1\t137899\t0\t1\t"
    )
    await _signal(
        db_session,
        headline="Fort Worth ISD outlines new strategy",
        state="TX",
        district="Fort Worth Independent School District",
    )

    out = await select_for_targets(db_session, days=1)

    assert out.targets == []
    assert out.excluded_not_target == 1
    assert out.unresolved == [], "a confident exclusion is not an unresolved one"


@pytest.mark.asyncio
async def test_statewide_news_reaches_him_where_he_has_targets(db_session) -> None:
    """A literacy bill belongs to no district but matters where he sells."""
    await _seed_targets(db_session, "Mack\tIL\tChicago Public Schools\tD1\t322000\t0\t1\t")
    await _signal(
        db_session,
        headline="IL SB1672: Early literacy screening requirement",
        state="IL",
        district=None,
    )

    out = await select_for_targets(db_session, days=1)

    assert len(out.state_level) == 1
    assert out.state_level[0].is_state_level
    assert out.targets == []


@pytest.mark.asyncio
async def test_statewide_news_from_a_state_he_does_not_sell_into_is_held_back(
    db_session,
) -> None:
    """This is what stops option 2 from reintroducing the firehose."""
    await _seed_targets(db_session, "Mack\tIL\tChicago Public Schools\tD1\t322000\t0\t1\t")
    await _signal(
        db_session, headline="Wyoming revises reading standards", state="WY", district=None
    )

    out = await select_for_targets(db_session, days=1)

    assert out.state_level == []
    assert out.excluded_other_state == 1


@pytest.mark.asyncio
async def test_an_unmatchable_district_is_held_separately_not_excluded(db_session) -> None:
    """Two PA districts normalize alike, so this one cannot be decided.

    It must land in `unresolved` — visible — rather than in the exclusion count,
    where a real opportunity would be lost to a naming collision.
    """
    await _seed_targets(
        db_session,
        "Kathleen\tPA\tHempfield Area School District\tD2\t6000\t0\t1\t",
        "Kathleen\tPA\tHempfield School District\tD3\t3000\t0\t1\t",
    )
    await _signal(
        db_session,
        headline="Hempfield schools adopt new curriculum",
        state="PA",
        district="Hempfield Schools",
    )

    out = await select_for_targets(db_session, days=1)

    assert out.targets == []
    assert out.excluded_not_target == 0
    assert len(out.unresolved) == 1
    assert "more than one" in out.unresolved[0].reason


@pytest.mark.asyncio
async def test_a_departed_account_stops_being_a_target(db_session) -> None:
    """Dropping off Josh's list usually means it became a customer."""
    await _seed_targets(
        db_session,
        "Natasha\tTX\tDallas Independent School District\tD1\t137899\t0\t1\t",
        "Natasha\tTX\tAustin Independent School District\tD1\t72773\t0\t1\t",
    )
    await _seed_targets(
        db_session, "Natasha\tTX\tDallas Independent School District\tD1\t137899\t0\t1\t"
    )
    await _signal(
        db_session,
        headline="Austin ISD news",
        state="TX",
        district="Austin Independent School District",
    )

    out = await select_for_targets(db_session, days=1)

    assert out.targets == [], "a departed account must not surface as a live target"
    assert out.excluded_not_target == 1


@pytest.mark.asyncio
async def test_the_summary_accounts_for_every_signal_considered(db_session) -> None:
    """Nothing may vanish silently between 'considered' and the buckets."""
    await _seed_targets(db_session, "Mack\tIL\tChicago Public Schools\tD1\t322000\t0\t1\t")
    await _signal(db_session, headline="CPS hires", state="IL", district="Chicago Public Schools")
    await _signal(db_session, headline="IL bill", state="IL", district=None)
    await _signal(db_session, headline="WY bill", state="WY", district=None)
    await _signal(db_session, headline="Peoria news", state="IL", district="Peoria District 150")

    out = await select_for_targets(db_session, days=1)

    counted = (
        len(out.targets)
        + len(out.state_level)
        + len(out.unresolved)
        + out.excluded_not_target
        + out.excluded_other_state
    )
    assert counted == out.considered == 4
    assert datetime.now(UTC) is not None
