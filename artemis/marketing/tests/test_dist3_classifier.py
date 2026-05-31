"""DIST3 — District Classifier agent + signal→district link acceptance tests.

6 required tests:
1. district.resolve exact match → correct district_id, high confidence.
2. district.resolve abbreviation ("LAUSD") → Los Angeles Unified (seeded fixture).
3. district.resolve with state hint disambiguates same-named districts in different states.
4. district.resolve no-match → returns no-match result, does NOT create or fabricate a district.
5. Signal resolution sets resolved_district_id on confident match; leaves NULL on no-match.
6. Lossless: legacy district_id text string preserved alongside the resolved FK.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import District, DistrictTierBand, SignalQueue
from artemis.tools.district_resolve import (
    ABBREVIATION_MAP,
    resolve_district_from_list,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def seed_tier_bands(db_session: AsyncSession) -> None:
    """Seed tier bands so upsert_district works when tests need it."""
    async with db_session.begin():
        db_session.add_all(
            [
                DistrictTierBand(
                    tier="D1",
                    min_enrollment=25000,
                    max_enrollment=None,
                    display_order=1,
                ),
                DistrictTierBand(
                    tier="D2",
                    min_enrollment=10000,
                    max_enrollment=24999,
                    display_order=2,
                ),
                DistrictTierBand(
                    tier="D3",
                    min_enrollment=5000,
                    max_enrollment=9999,
                    display_order=3,
                ),
                DistrictTierBand(
                    tier="D4",
                    min_enrollment=None,
                    max_enrollment=4999,
                    display_order=4,
                ),
            ]
        )


@pytest.fixture
def lausd_district() -> District:
    """In-memory District fixture for Los Angeles Unified."""
    return District(
        id=1001,
        nces_id="0622710",
        name="Los Angeles Unified",
        state="CA",
        enrollment=600000,
        tier="D1",
        supported=True,
        on_skip_list=False,
        classification_source="nces",
    )


@pytest.fixture
def springfield_il() -> District:
    """Springfield, IL district for disambiguation test."""
    return District(
        id=2001,
        nces_id="1700100",
        name="Springfield School District",
        state="IL",
        enrollment=8000,
        tier="D3",
        supported=True,
        on_skip_list=False,
        classification_source="nces",
    )


@pytest.fixture
def springfield_mo() -> District:
    """Springfield, MO district for disambiguation test."""
    return District(
        id=2002,
        nces_id="2900100",
        name="Springfield School District",
        state="MO",
        enrollment=25000,
        tier="D1",
        supported=True,
        on_skip_list=False,
        classification_source="nces",
    )


@pytest.fixture
def small_district() -> District:
    """An unrelated small district — should NOT match Los Angeles queries."""
    return District(
        id=3001,
        nces_id="9900001",
        name="Tiny Valley School District",
        state="WY",
        enrollment=300,
        tier="D4",
        supported=False,
        on_skip_list=False,
        classification_source="nces",
    )


# ---------------------------------------------------------------------------
# Test 1: exact match → correct district_id, high confidence
# ---------------------------------------------------------------------------


async def test_resolve_exact_match(lausd_district: District, small_district: District) -> None:
    """Exact name match (after normalisation) returns high confidence and the correct id."""
    result = resolve_district_from_list(
        "Los Angeles Unified",
        None,
        [lausd_district, small_district],
    )
    assert result.matched is True
    assert result.district_id == lausd_district.id
    assert result.confidence >= 0.90
    assert result.match_method == "exact"


# ---------------------------------------------------------------------------
# Test 2: abbreviation "LAUSD" → Los Angeles Unified
# ---------------------------------------------------------------------------


async def test_resolve_abbreviation_lausd(
    lausd_district: District, small_district: District
) -> None:
    """LAUSD abbreviation expands to 'los angeles unified' and matches the seeded fixture."""
    # Confirm the abbreviation map entry exists
    assert "lausd" in ABBREVIATION_MAP
    assert "los angeles unified" in ABBREVIATION_MAP["lausd"]

    result = resolve_district_from_list(
        "LAUSD",
        "CA",
        [lausd_district, small_district],
    )
    assert result.matched is True
    assert result.district_id == lausd_district.id
    assert result.district_name == "Los Angeles Unified"
    assert result.confidence >= 0.90


# ---------------------------------------------------------------------------
# Test 3: state hint disambiguates same-named districts
# ---------------------------------------------------------------------------


async def test_resolve_state_hint_disambiguates(
    springfield_il: District,
    springfield_mo: District,
) -> None:
    """When two districts share a name, the state hint picks the correct one."""
    all_districts = [springfield_il, springfield_mo]

    result_il = resolve_district_from_list(
        "Springfield School District",
        "IL",
        all_districts,
    )
    assert result_il.matched is True
    assert result_il.district_id == springfield_il.id
    assert result_il.district_state == "IL"

    result_mo = resolve_district_from_list(
        "Springfield School District",
        "MO",
        all_districts,
    )
    assert result_mo.matched is True
    assert result_mo.district_id == springfield_mo.id
    assert result_mo.district_state == "MO"


# ---------------------------------------------------------------------------
# Test 4: no-match → result.matched is False, no district created or fabricated
# ---------------------------------------------------------------------------


async def test_resolve_no_match_does_not_fabricate(
    db_session: AsyncSession,
    small_district: District,
) -> None:
    """A name that matches nothing returns no-match; district count stays unchanged."""
    # Seed one real district row in the DB.
    async with db_session.begin():
        db_session.add(
            District(
                nces_id="NCES-9999",
                name="Tiny Valley School District",
                state="WY",
                enrollment=300,
                tier="D4",
                supported=False,
                on_skip_list=False,
                classification_source="nces",
            )
        )

    district_count_before = await db_session.scalar(select(func.count()).select_from(District))
    assert district_count_before == 1

    # Pure resolver — name that cannot match anything in the list.
    result = resolve_district_from_list(
        "Totally Unknown XYZ School District 999",
        None,
        [small_district],
    )
    assert result.matched is False
    assert result.district_id is None

    # DB row count must not have changed.
    district_count_after = await db_session.scalar(select(func.count()).select_from(District))
    assert district_count_after == district_count_before


# ---------------------------------------------------------------------------
# Test 5: signal resolution sets resolved_district_id on match; NULL on no-match
# ---------------------------------------------------------------------------


async def test_signal_resolution_sets_and_leaves_null(db_session: AsyncSession) -> None:
    """resolved_district_id is set on match, stays NULL when district is missing."""
    from artemis.marketing.repository import upsert_district
    from artemis.tools.district_resolve import resolve_district

    # Seed a real district in the DB.
    async with db_session.begin():
        seeded = await upsert_district(
            db_session,
            nces_id="NCES-LAUSD",
            name="Los Angeles Unified",
            state="CA",
            enrollment=600000,
            source="nces",
        )

    # 1. A signal with a matching district name.
    async with db_session.begin():
        matched_signal = SignalQueue(
            source_type="manual",
            headline="LAUSD adopts new literacy program",
            summary="LAUSD literacy update",
            campaign_family="obc",
            urgency_tier="standard",
            district_id="LAUSD",
            state="CA",
            signal_status="pending_qualification",
        )
        db_session.add(matched_signal)
        await db_session.flush()

        resolve_result = await resolve_district(db_session, "LAUSD", "CA")
        assert resolve_result.matched is True
        assert resolve_result.district_id == seeded.id
        matched_signal.resolved_district_id = resolve_result.district_id

    refreshed = await db_session.get(SignalQueue, matched_signal.id)
    assert refreshed is not None
    assert refreshed.resolved_district_id == seeded.id

    # 2. A signal with a district name that cannot be resolved.
    async with db_session.begin():
        unmatched_signal = SignalQueue(
            source_type="manual",
            headline="Unknown District literacy initiative",
            summary="Unknown district update",
            campaign_family="obc",
            urgency_tier="standard",
            district_id="Nowheresville Unified XYZ",
            state="CA",
            signal_status="pending_qualification",
        )
        db_session.add(unmatched_signal)
        await db_session.flush()

        no_match = await resolve_district(db_session, "Nowheresville Unified XYZ", "CA")
        assert no_match.matched is False
        # Do NOT set resolved_district_id — leave NULL.
        # (Confirming we don't touch it here is the test.)

    refreshed_unmatched = await db_session.get(SignalQueue, unmatched_signal.id)
    assert refreshed_unmatched is not None
    assert refreshed_unmatched.resolved_district_id is None


# ---------------------------------------------------------------------------
# Test 6: lossless — legacy district_id text preserved alongside resolved FK
# ---------------------------------------------------------------------------


async def test_lossless_legacy_district_id_preserved(db_session: AsyncSession) -> None:
    """resolved_district_id FK is additive; the raw district_id text string survives."""
    from artemis.marketing.repository import upsert_district

    # Seed district
    async with db_session.begin():
        seeded = await upsert_district(
            db_session,
            nces_id="NCES-CHICAGO",
            name="Chicago Public Schools",
            state="IL",
            enrollment=350000,
            source="nces",
        )

    raw_name = "CPS"

    async with db_session.begin():
        signal = SignalQueue(
            source_type="manual",
            headline="CPS announces new reading curriculum",
            summary="CPS reading update",
            campaign_family="obc",
            urgency_tier="standard",
            district_id=raw_name,  # raw scraped text — must never be overwritten
            state="IL",
            signal_status="pending_qualification",
        )
        db_session.add(signal)
        await db_session.flush()

        from artemis.tools.district_resolve import resolve_district

        result = await resolve_district(db_session, raw_name, "IL")
        assert result.matched is True
        signal.resolved_district_id = result.district_id

    refreshed = await db_session.get(SignalQueue, signal.id)
    assert refreshed is not None
    # Legacy raw text must be untouched
    assert refreshed.district_id == raw_name
    # Resolved FK set to the canonical district
    assert refreshed.resolved_district_id == seeded.id
