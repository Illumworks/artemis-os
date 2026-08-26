"""Target-account matching.

Every case here came out of profiling Josh's real 1,287-row list on 2026-08-25,
not from imagining shapes. The two that matter most — Hempfield and Community
ISD — would each have shipped as a silent wrong answer.
"""

from __future__ import annotations

import pytest

from artemis.marketing.models import District
from artemis.marketing.targets.matching import (
    Verdict,
    classify_district,
    normalize_district_name,
)
from artemis.marketing.targets.models import TargetAccount


async def _target(session, name: str, state: str, **kw) -> TargetAccount:
    row = TargetAccount(
        account_name=name,
        state=state,
        normalized_name=normalize_district_name(name) or None,
        **kw,
    )
    session.add(row)
    await session.flush()
    return row


# ── Normalization ────────────────────────────────────────────────────────────


def test_nces_local_codes_are_stripped() -> None:
    """NCES suffixes a local code; Salesforce never does.

    Stripping these alone moved the match rate from 49.5% to 80.5% on the live
    list, which is why the rule exists.
    """
    assert normalize_district_name("Mesa Unified District (4235)") == "MESA"
    assert normalize_district_name("Chandler Unified District #80 (4242)") == "CHANDLER"


def test_generic_words_alone_normalize_to_nothing() -> None:
    """ "Community Independent School District" (a real TX account) is all stopwords.

    It must produce an EMPTY key, not a short one — an empty key used for
    matching would behave as a wildcard and hit every district in the state.
    """
    assert normalize_district_name("Community Independent School District") == ""


def test_normalization_is_mild_enough_to_keep_distinct_names_distinct() -> None:
    assert normalize_district_name("Dallas ISD") == "DALLAS"
    assert normalize_district_name("Austin Independent School District") == "AUSTIN"
    assert normalize_district_name("Dallas ISD") != normalize_district_name("Austin ISD")


# ── The three verdicts ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exact_name_and_state_is_a_target(db_session) -> None:
    await _target(db_session, "Dallas Independent School District", "TX", marketing_tier="D1")

    out = await classify_district(
        db_session, district_name="Dallas Independent School District", state="TX"
    )
    assert out.verdict is Verdict.TARGET
    assert out.is_target
    assert out.marketing_tier == "D1"


@pytest.mark.asyncio
async def test_normalized_name_matches_across_naming_conventions(db_session) -> None:
    """Salesforce's "Mesa Public Schools" and NCES's "Mesa Unified District (4235)"."""
    await _target(db_session, "Mesa Public Schools", "AZ")

    out = await classify_district(
        db_session, district_name="Mesa Unified District (4235)", state="AZ"
    )
    assert out.verdict is Verdict.TARGET
    assert out.account_name == "Mesa Public Schools"


@pytest.mark.asyncio
async def test_a_district_absent_from_the_list_is_not_a_target(db_session) -> None:
    """THE complaint: Fort Worth ISD is genuinely not on Josh's list."""
    await _target(db_session, "Dallas Independent School District", "TX")

    out = await classify_district(
        db_session, district_name="Fort Worth Independent School District", state="TX"
    )
    assert out.verdict is Verdict.NOT_TARGET
    assert "not in the target account list" in out.reason


# ── Abstaining — the cases that must NOT be guessed ──────────────────────────


@pytest.mark.asyncio
async def test_two_pa_districts_that_normalize_alike_abstain(db_session) -> None:
    """Hempfield Area SD and Hempfield SD are different PA districts.

    They reduce to the same key. Picking either is a coin flip on a live sales
    target, so the answer is UNKNOWN and it names both candidates.
    """
    await _target(db_session, "Hempfield Area School District", "PA")
    await _target(db_session, "Hempfield School District", "PA")

    out = await classify_district(db_session, district_name="Hempfield Schools", state="PA")
    assert out.verdict is Verdict.UNKNOWN
    assert "more than one" in out.reason
    assert "Hempfield Area School District" in out.reason


@pytest.mark.asyncio
async def test_an_all_generic_name_abstains_instead_of_wildcarding(db_session) -> None:
    """An empty normalized key must never match everything in the state."""
    await _target(db_session, "Dallas Independent School District", "TX")
    await _target(db_session, "Austin Independent School District", "TX")

    out = await classify_district(
        db_session, district_name="Community Independent School District", state="TX"
    )
    assert out.verdict is Verdict.UNKNOWN
    assert "generic words" in out.reason


@pytest.mark.asyncio
async def test_a_signal_with_no_district_is_unknown_not_excluded(db_session) -> None:
    """State-level legislative signals name no district.

    UNKNOWN, never NOT_TARGET — Josh should decide whether a state bill matters,
    rather than have it silently filtered out of his view.
    """
    await _target(db_session, "Dallas Independent School District", "TX")

    out = await classify_district(db_session, district_name=None, state="IL")
    assert out.verdict is Verdict.UNKNOWN


@pytest.mark.asyncio
async def test_a_district_without_a_state_is_unknown(db_session) -> None:
    """District names are only unique within a state — matching without one guesses."""
    await _target(db_session, "Lincoln Public Schools", "NE")

    out = await classify_district(db_session, district_name="Lincoln Public Schools", state=None)
    assert out.verdict is Verdict.UNKNOWN
    assert "only unique within a state" in out.reason


@pytest.mark.asyncio
async def test_the_same_name_in_two_states_does_not_cross_over(db_session) -> None:
    """13 account names repeat across states in the live list."""
    await _target(db_session, "Jefferson County Schools", "KY")

    out = await classify_district(db_session, district_name="Jefferson County Schools", state="AL")
    assert out.verdict is Verdict.NOT_TARGET


# ── The resolved-district-id fast path ───────────────────────────────────────


@pytest.mark.asyncio
async def test_resolved_district_id_matches_without_touching_names(db_session) -> None:
    district = District(name="DALLAS ISD", state="TX")
    db_session.add(district)
    await db_session.flush()

    await _target(db_session, "Dallas Independent School District", "TX", district_id=district.id)

    out = await classify_district(
        db_session, district_name=None, state=None, district_id=district.id
    )
    assert out.verdict is Verdict.TARGET
    assert "resolved district id" in out.reason


@pytest.mark.asyncio
async def test_an_unlinked_district_id_falls_through_to_name_matching(db_session) -> None:
    """Only ~80% of target rows carry a district_id, so an absent link proves nothing."""
    district = District(name="DALLAS ISD", state="TX")
    db_session.add(district)
    await db_session.flush()
    await _target(db_session, "Dallas Independent School District", "TX")  # no district_id

    out = await classify_district(
        db_session,
        district_name="Dallas Independent School District",
        state="TX",
        district_id=district.id,
    )
    assert out.verdict is Verdict.TARGET
