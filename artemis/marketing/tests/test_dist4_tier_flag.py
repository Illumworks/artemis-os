"""DIST4 — Qualifier soft-flag acceptance tests.

4 required tests:
1. Qualification of a signal linked to a D4 district → district_supported=False,
   tier_flag="unsupported_tier", but signal STILL reaches Gate 1 (not dropped).
2. Qualification of a D1-district signal → district_supported=True, no flag.
3. Signal with NULL resolved_district_id → no tier annotation, no crash.
4. Lossless/soft: assert the unsupported signal's status path is identical to a
   supported one (only metadata differs) — no auto-skip.

Storage: qualification_json.districtContext (no migration — additive key).
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import (
    District,
    DistrictTierBand,
    Ruleset,
    SignalQueue,
    TerritoryConfig,
)
from artemis.marketing.qualifier import annotate_district_tier

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def seed_tier_bands(db_session: AsyncSession) -> None:
    """Seed the standard D1–D4 bands so upsert_district/qualify paths work."""
    async with db_session.begin():
        db_session.add_all(
            [
                DistrictTierBand(
                    tier="D1", min_enrollment=25000, max_enrollment=None, display_order=1
                ),
                DistrictTierBand(
                    tier="D2", min_enrollment=10000, max_enrollment=24999, display_order=2
                ),
                DistrictTierBand(
                    tier="D3", min_enrollment=5000, max_enrollment=9999, display_order=3
                ),
                DistrictTierBand(
                    tier="D4", min_enrollment=None, max_enrollment=4999, display_order=4
                ),
            ]
        )


@pytest.fixture
async def ruleset(db_session: AsyncSession) -> Ruleset:
    """Active ruleset for the 'obc' campaign family — needed for qualify path."""
    async with db_session.begin():
        rs = Ruleset(
            family="obc",
            version_tag="v1",
            state="active",
            hard_filters=[],
            weighted_signals=[{"reason_code": "POLICY_WIN", "weight": 0.8}],
            qualitative_rubrics=[],
        )
        db_session.add(rs)
        await db_session.flush()
        await db_session.refresh(rs)
    return rs


@pytest.fixture
async def territory(db_session: AsyncSession) -> TerritoryConfig:
    """Territory config for 'obc' family — IN is a hot state."""
    async with db_session.begin():
        tc = TerritoryConfig(
            family="obc",
            hot_states=["IN"],
            standard_states=["FL", "TX"],
        )
        db_session.add(tc)
        await db_session.flush()
    return tc


@pytest.fixture
async def d4_district(db_session: AsyncSession) -> District:
    """A D4 (unsupported) district — enrollment < 5,000."""
    async with db_session.begin():
        d = District(
            nces_id="D4-TEST-001",
            name="Tinytown SD",
            state="MT",
            enrollment=1100,
            tier="D4",
            supported=False,
            on_skip_list=False,
            classification_source="nces",
        )
        db_session.add(d)
        await db_session.flush()
        await db_session.refresh(d)
    return d


@pytest.fixture
async def d1_district(db_session: AsyncSession) -> District:
    """A D1 (supported) district — enrollment >= 25,000."""
    async with db_session.begin():
        d = District(
            nces_id="D1-TEST-001",
            name="Los Angeles Unified",
            state="CA",
            enrollment=600000,
            tier="D1",
            supported=True,
            on_skip_list=False,
            classification_source="nces",
        )
        db_session.add(d)
        await db_session.flush()
        await db_session.refresh(d)
    return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_qual_dict() -> dict[str, object]:
    """A minimal qualification_json dict without districtContext."""
    return {
        "qualifiedAt": "2026-05-31T00:00:00+00:00",
        "rulesetVersionsUsed": {"obc": "v1"},
        "scores": [
            {
                "campaignFamily": "obc",
                "passedHardFilters": True,
                "adjustedScore": 0.75,
                "passesMinFitScore": True,
            }
        ],
        "recommendedFamilies": [
            {"campaignFamily": "obc", "role": "primary", "adjustedScore": 0.75}
        ],
    }


# ---------------------------------------------------------------------------
# Test 1 — D4 district signal is soft-flagged but NOT dropped
# ---------------------------------------------------------------------------


async def test_d4_signal_annotated_unsupported_tier(
    db_session: AsyncSession,
    ruleset: Ruleset,
    territory: TerritoryConfig,
    d4_district: District,
) -> None:
    """A signal linked to a D4 district gets tier_flag='unsupported_tier'.

    The signal is NOT dropped, rejected, or auto-skipped — soft flag only.
    """
    qual = _minimal_qual_dict()
    annotated = annotate_district_tier(
        qual,
        district_id=d4_district.id,
        district_name=d4_district.name,
        district_state=d4_district.state,
        district_tier=d4_district.tier,
        district_enrollment=d4_district.enrollment,
        district_supported=d4_district.supported,
    )

    ctx = annotated["districtContext"]
    assert ctx["resolved"] is True
    assert ctx["districtSupported"] is False
    assert ctx["tierFlag"] == "unsupported_tier"
    assert ctx["districtTier"] == "D4"
    assert ctx["districtName"] == "Tinytown SD"
    assert ctx["districtEnrollment"] == 1100

    # The underlying qualification data (scores/families) is untouched — signal not dropped
    assert annotated["recommendedFamilies"] == qual["recommendedFamilies"]
    assert annotated["scores"] == qual["scores"]


# ---------------------------------------------------------------------------
# Test 2 — D1 district signal: supported=True, no flag
# ---------------------------------------------------------------------------


async def test_d1_signal_annotated_supported_no_flag(
    db_session: AsyncSession,
    ruleset: Ruleset,
    territory: TerritoryConfig,
    d1_district: District,
) -> None:
    """A signal linked to a D1 district gets districtSupported=True and no tier_flag."""
    qual = _minimal_qual_dict()
    annotated = annotate_district_tier(
        qual,
        district_id=d1_district.id,
        district_name=d1_district.name,
        district_state=d1_district.state,
        district_tier=d1_district.tier,
        district_enrollment=d1_district.enrollment,
        district_supported=d1_district.supported,
    )

    ctx = annotated["districtContext"]
    assert ctx["resolved"] is True
    assert ctx["districtSupported"] is True
    assert ctx["tierFlag"] is None
    assert ctx["districtTier"] == "D1"


# ---------------------------------------------------------------------------
# Test 3 — NULL resolved_district_id: no annotation, no crash
# ---------------------------------------------------------------------------


async def test_null_resolved_district_id_no_crash() -> None:
    """Signal with no resolved district gets districtContext.resolved=False, no fabrication."""
    qual = _minimal_qual_dict()
    annotated = annotate_district_tier(
        qual,
        district_id=None,
        district_name=None,
        district_state=None,
        district_tier=None,
        district_enrollment=None,
        district_supported=None,
    )

    ctx = annotated["districtContext"]
    assert ctx["resolved"] is False
    # No fabricated tier/name/enrollment
    assert "districtTier" not in ctx
    assert "districtName" not in ctx
    assert "tierFlag" not in ctx
    # The rest of qualification_json is intact
    assert annotated["scores"] == qual["scores"]


# ---------------------------------------------------------------------------
# Test 4 — Lossless / soft: D4 signal status path identical to supported signal
# ---------------------------------------------------------------------------


async def test_soft_flag_lossless_status_path_unchanged(
    db_session: AsyncSession,
    ruleset: Ruleset,
    territory: TerritoryConfig,
    d4_district: District,
    d1_district: District,
) -> None:
    """D4 soft-flag does NOT alter the signal's status or qualification scores.

    Both a D4-linked and a D1-linked signal start at 'pending_qualification'
    and remain there after annotation — no auto-skip, no status change.
    The ONLY difference is the districtContext metadata.
    """
    # Insert a D4-linked signal
    async with db_session.begin():
        d4_signal = SignalQueue(
            source_type="manual",
            headline="Tinytown SD reading program update",
            summary="Small district reading signal",
            campaign_family="obc",
            urgency_tier="standard",
            district_id="Tinytown SD",
            resolved_district_id=d4_district.id,
            state="MT",
            reason_codes=[{"code": "POLICY_WIN", "confidence": 0.9}],
            signal_status="pending_qualification",
        )
        db_session.add(d4_signal)

        # Insert a D1-linked signal
        d1_signal = SignalQueue(
            source_type="manual",
            headline="LAUSD reading program update",
            summary="Large district reading signal",
            campaign_family="obc",
            urgency_tier="standard",
            district_id="LAUSD",
            resolved_district_id=d1_district.id,
            state="CA",
            reason_codes=[{"code": "POLICY_WIN", "confidence": 0.9}],
            signal_status="pending_qualification",
        )
        db_session.add(d1_signal)
        await db_session.flush()
        await db_session.refresh(d4_signal)
        await db_session.refresh(d1_signal)

    # Annotate both signals with the same base qual dict
    qual_base = _minimal_qual_dict()

    d4_annotated = annotate_district_tier(
        qual_base,
        district_id=d4_district.id,
        district_name=d4_district.name,
        district_state=d4_district.state,
        district_tier=d4_district.tier,
        district_enrollment=d4_district.enrollment,
        district_supported=d4_district.supported,
    )
    d1_annotated = annotate_district_tier(
        qual_base,
        district_id=d1_district.id,
        district_name=d1_district.name,
        district_state=d1_district.state,
        district_tier=d1_district.tier,
        district_enrollment=d1_district.enrollment,
        district_supported=d1_district.supported,
    )

    # Status path IDENTICAL: both remain pending_qualification
    refreshed_d4 = await db_session.get(SignalQueue, d4_signal.id)
    refreshed_d1 = await db_session.get(SignalQueue, d1_signal.id)
    assert refreshed_d4 is not None and refreshed_d4.signal_status == "pending_qualification"
    assert refreshed_d1 is not None and refreshed_d1.signal_status == "pending_qualification"

    # Scores and recommendedFamilies are IDENTICAL between D4 and D1 paths
    assert d4_annotated["scores"] == d1_annotated["scores"]
    assert d4_annotated["recommendedFamilies"] == d1_annotated["recommendedFamilies"]

    # Only districtContext differs
    assert d4_annotated["districtContext"]["tierFlag"] == "unsupported_tier"
    assert d1_annotated["districtContext"]["tierFlag"] is None
    assert d4_annotated["districtContext"]["districtSupported"] is False
    assert d1_annotated["districtContext"]["districtSupported"] is True
