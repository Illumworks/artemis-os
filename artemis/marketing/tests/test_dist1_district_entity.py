"""DIST1 district entity + NCES loader acceptance tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.district_classifier import classify_tier, is_supported
from artemis.marketing.models import District, DistrictTierBand
from artemis.marketing.nces_loader import load_districts_from_csv
from artemis.marketing.repository import (
    recompute_all_tiers,
    upsert_district,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seed_tier_bands(db_session: AsyncSession) -> None:
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


def _bands() -> list[DistrictTierBand]:
    return [
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


async def test_classify_tier_boundaries() -> None:
    bands = _bands()
    assert classify_tier(24999, bands) == "D2"
    assert classify_tier(25000, bands) == "D1"
    assert classify_tier(9999, bands) == "D3"
    assert classify_tier(10000, bands) == "D2"
    assert classify_tier(4999, bands) == "D4"
    assert classify_tier(5000, bands) == "D3"
    assert classify_tier(0, bands) == "D4"
    assert classify_tier(None, bands) is None


async def test_is_supported() -> None:
    assert is_supported("D1") is True
    assert is_supported("D2") is True
    assert is_supported("D3") is True
    assert is_supported("D4") is False
    assert is_supported(None) is False


async def test_upsert_district_inserts_then_updates_in_place(db_session: AsyncSession) -> None:
    async with db_session.begin():
        district = await upsert_district(
            db_session,
            nces_id="NCES-001",
            name="Alpha Unified",
            state="CA",
            enrollment=12000,
            source="nces",
        )
        first_id = district.id

        updated = await upsert_district(
            db_session,
            nces_id="NCES-001",
            name="Alpha Unified District",
            state="CA",
            enrollment=13000,
            source="nces",
        )

        assert updated.id == first_id
        assert updated.name == "Alpha Unified District"
        assert updated.enrollment == 13000

        row_count = await db_session.scalar(
            select(func.count()).select_from(District).where(District.nces_id == "NCES-001")
        )
        assert row_count == 1


async def test_upsert_district_soft_flags_d4_without_deleting_row(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await upsert_district(
            db_session,
            nces_id="NCES-004",
            name="Delta District",
            state="TX",
            enrollment=3000,
            source="manual",
        )

        assert district.tier == "D4"
        assert district.supported is False
        assert district.classification_source == "manual"

        persisted = await db_session.get(District, district.id)
        assert persisted is not None
        assert persisted.tier == "D4"
        assert persisted.supported is False


async def test_recompute_all_tiers_reclassifies_after_band_change(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        district = await upsert_district(
            db_session,
            nces_id="NCES-005",
            name="Echo District",
            state="WA",
            enrollment=9000,
            source="nces",
        )
        assert district.tier == "D3"

        d2 = await db_session.scalar(select(DistrictTierBand).where(DistrictTierBand.tier == "D2"))
        d3 = await db_session.scalar(select(DistrictTierBand).where(DistrictTierBand.tier == "D3"))
        assert d2 is not None
        assert d3 is not None
        d2.min_enrollment = 8000
        d2.max_enrollment = 9000
        d3.min_enrollment = 5000
        d3.max_enrollment = 7999
        await db_session.flush()

        updated_count = await recompute_all_tiers(db_session)
        assert updated_count == 1

        refreshed = await db_session.get(District, district.id)
        assert refreshed is not None
        assert refreshed.tier == "D2"
        assert refreshed.supported is True


async def test_load_districts_from_csv_fixture_is_idempotent(
    db_session: AsyncSession,
) -> None:
    fixture_path = Path(__file__).with_name("fixtures") / "nces_sample.csv"

    async with db_session.begin():
        first = await load_districts_from_csv(db_session, fixture_path)
        assert first == {"loaded": 7, "skipped": 1}

        tiers = {
            tier for tier in await db_session.scalars(select(District.tier)) if tier is not None
        }
        assert tiers == {"D1", "D2", "D3", "D4"}

        second = await load_districts_from_csv(db_session, fixture_path)
        assert second == {"loaded": 7, "skipped": 1}

        row_count = await db_session.scalar(select(func.count()).select_from(District))
        assert row_count == 7


async def test_load_districts_from_csv_bad_header_raises(
    tmp_path: Path, db_session: AsyncSession
) -> None:
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("nces_id,name,enrollment\n1,Alpha,123\n", encoding="utf-8")

    async with db_session.begin():
        with pytest.raises(ValueError, match=r"expected columns .*'state'.*found"):
            await load_districts_from_csv(db_session, bad_csv)
