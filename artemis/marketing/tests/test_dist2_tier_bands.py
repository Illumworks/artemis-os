"""DIST2 — tier-band editor endpoints + tiling validation tests.

Endpoints under test:
  GET  /api/signal-criteria/tier-bands
  PUT  /api/signal-criteria/tier-bands
  POST /api/signal-criteria/tier-bands/recompute

Run with:
  ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_dist2 \
    uv run pytest artemis/marketing/tests/test_dist2_tier_bands.py -v
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import District, DistrictTierBand

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_BANDS = [
    {"tier": "D1", "minEnrollment": 25000, "maxEnrollment": None, "displayOrder": 1},
    {"tier": "D2", "minEnrollment": 10000, "maxEnrollment": 24999, "displayOrder": 2},
    {"tier": "D3", "minEnrollment": 5000, "maxEnrollment": 9999, "displayOrder": 3},
    {"tier": "D4", "minEnrollment": None, "maxEnrollment": 4999, "displayOrder": 4},
]


async def _seed_bands(session: AsyncSession) -> None:
    """Seed default tier bands into the test DB."""
    async with session.begin():
        session.add_all(
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


async def _seed_districts(session: AsyncSession) -> None:
    """Seed a couple districts so recompute has something to operate on."""
    async with session.begin():
        session.add_all(
            [
                District(
                    name="Riverside Unified",
                    state="CA",
                    enrollment=28000,
                    tier="D1",
                    supported=True,
                    on_skip_list=False,
                    classification_source="manual",
                ),
                District(
                    name="Smallville USD",
                    state="KS",
                    enrollment=3000,
                    tier="D4",
                    supported=False,
                    on_skip_list=False,
                    classification_source="manual",
                ),
            ]
        )


# ---------------------------------------------------------------------------
# Test 1 — GET returns 4 seeded bands ordered by display_order
# ---------------------------------------------------------------------------


async def test_get_tier_bands_returns_four_ordered(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_bands(db_session)

    response = await client.get("/api/signal-criteria/tier-bands")

    assert response.status_code == 200
    body = response.json()
    assert "bands" in body
    bands = body["bands"]
    assert len(bands) == 4
    tiers = [b["tier"] for b in bands]
    assert tiers == ["D1", "D2", "D3", "D4"]
    assert bands[0]["minEnrollment"] == 25000
    assert bands[0]["maxEnrollment"] is None
    assert bands[3]["minEnrollment"] is None
    assert bands[3]["maxEnrollment"] == 4999


# ---------------------------------------------------------------------------
# Test 2 — PUT with valid tiling updates bands; re-GET reflects the change
# ---------------------------------------------------------------------------


async def test_put_valid_bands_updates_and_reflects(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_bands(db_session)

    # Change D3 max from 9999 to 12499, and adjust D2 min from 10000 to 12500
    new_bands = [
        {"tier": "D1", "minEnrollment": 25000, "maxEnrollment": None, "displayOrder": 1},
        {"tier": "D2", "minEnrollment": 12500, "maxEnrollment": 24999, "displayOrder": 2},
        {"tier": "D3", "minEnrollment": 5000, "maxEnrollment": 12499, "displayOrder": 3},
        {"tier": "D4", "minEnrollment": None, "maxEnrollment": 4999, "displayOrder": 4},
    ]

    put_response = await client.put("/api/signal-criteria/tier-bands", json={"bands": new_bands})
    assert put_response.status_code == 200
    put_body = put_response.json()
    d2 = next(b for b in put_body["bands"] if b["tier"] == "D2")
    assert d2["minEnrollment"] == 12500

    # Verify re-GET reflects the saved change
    get_response = await client.get("/api/signal-criteria/tier-bands")
    assert get_response.status_code == 200
    get_d3 = next(b for b in get_response.json()["bands"] if b["tier"] == "D3")
    assert get_d3["maxEnrollment"] == 12499


# ---------------------------------------------------------------------------
# Test 3 — PUT with a gap is rejected with a self-teaching message naming it
# ---------------------------------------------------------------------------


async def test_put_with_gap_rejected_self_teaching(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_bands(db_session)

    # Gap: D3 max = 9999, D2 min = 11000 → values 10000–10999 uncovered
    gapped_bands = [
        {"tier": "D1", "minEnrollment": 25000, "maxEnrollment": None, "displayOrder": 1},
        {"tier": "D2", "minEnrollment": 11000, "maxEnrollment": 24999, "displayOrder": 2},
        {"tier": "D3", "minEnrollment": 5000, "maxEnrollment": 9999, "displayOrder": 3},
        {"tier": "D4", "minEnrollment": None, "maxEnrollment": 4999, "displayOrder": 4},
    ]

    response = await client.put("/api/signal-criteria/tier-bands", json={"bands": gapped_bands})

    assert response.status_code == 400
    body = response.json()
    assert "gap" in body["error"].lower() or "gap" in body.get("code", "").lower()
    # Self-teaching: names which tiers are involved
    assert "D3" in body["error"]
    assert "D2" in body["error"]


# ---------------------------------------------------------------------------
# Test 4 — PUT with an overlap is rejected with self-teaching message
# ---------------------------------------------------------------------------


async def test_put_with_overlap_rejected_self_teaching(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_bands(db_session)

    # Overlap: D3 max = 10500, D2 min = 10000 → 10000–10500 covered by both
    overlapping_bands = [
        {"tier": "D1", "minEnrollment": 25000, "maxEnrollment": None, "displayOrder": 1},
        {"tier": "D2", "minEnrollment": 10000, "maxEnrollment": 24999, "displayOrder": 2},
        {"tier": "D3", "minEnrollment": 5000, "maxEnrollment": 10500, "displayOrder": 3},
        {"tier": "D4", "minEnrollment": None, "maxEnrollment": 4999, "displayOrder": 4},
    ]

    response = await client.put(
        "/api/signal-criteria/tier-bands", json={"bands": overlapping_bands}
    )

    assert response.status_code == 400
    body = response.json()
    assert "overlap" in body["error"].lower() or "overlap" in body.get("code", "").lower()
    assert "D3" in body["error"]
    assert "D2" in body["error"]


# ---------------------------------------------------------------------------
# Test 5 — POST recompute after a band change returns updated count > 0
# ---------------------------------------------------------------------------


async def test_post_recompute_returns_updated_count(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_bands(db_session)
    await _seed_districts(db_session)

    # Verify districts seeded
    get_bands = await client.get("/api/signal-criteria/tier-bands")
    assert get_bands.status_code == 200
    assert len(get_bands.json()["bands"]) == 4

    # Recompute
    response = await client.post("/api/signal-criteria/tier-bands/recompute")

    assert response.status_code == 200
    body = response.json()
    assert "updated" in body
    assert body["updated"] >= 2  # at least the 2 seeded districts
