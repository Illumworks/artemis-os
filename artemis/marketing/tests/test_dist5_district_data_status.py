"""DIST5 — district data freshness panel tests.

Tests:
  1. Endpoint with stamped meta + seeded districts → correct counts, tier_counts,
     supported/unsupported.
  2. Freshness thresholds: 0 mo → "current"; 14 mo → "aging"; 20 mo → "stale".
  3. Empty district_data_meta → "no data loaded" shape (no crash, no fabricated numbers).
  4. Loader stamps district_data_meta on load (school_year + row_count correct).
  5. #100 — POST /district-data-refresh spawns a subprocess and returns 202;
     a second call while one is in flight returns 409.

Run with:
  ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/artemis_test_dist5 \\
    uv run pytest artemis/marketing/tests/test_dist5_district_data_status.py -v
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.models import District, DistrictDataMeta, DistrictTierBand

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_bands() -> list[DistrictTierBand]:
    return [
        DistrictTierBand(tier="D1", min_enrollment=25000, max_enrollment=None, display_order=1),
        DistrictTierBand(tier="D2", min_enrollment=10000, max_enrollment=24999, display_order=2),
        DistrictTierBand(tier="D3", min_enrollment=5000, max_enrollment=9999, display_order=3),
        DistrictTierBand(tier="D4", min_enrollment=None, max_enrollment=4999, display_order=4),
    ]


async def _seed_bands(session: AsyncSession) -> None:
    async with session.begin():
        session.add_all(_default_bands())


async def _seed_districts(session: AsyncSession) -> None:
    """Seed a representative district sample across all 4 tiers."""
    async with session.begin():
        session.add_all(
            [
                # D1 — 2 rows
                District(
                    name="Los Angeles USD",
                    state="CA",
                    enrollment=570000,
                    tier="D1",
                    supported=True,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                District(
                    name="Chicago Public Schools",
                    state="IL",
                    enrollment=342000,
                    tier="D1",
                    supported=True,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                # D2 — 3 rows
                District(
                    name="Springfield USD",
                    state="IL",
                    enrollment=15000,
                    tier="D2",
                    supported=True,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                District(
                    name="Riverside USD",
                    state="CA",
                    enrollment=12000,
                    tier="D2",
                    supported=True,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                District(
                    name="Westside USD",
                    state="TX",
                    enrollment=11000,
                    tier="D2",
                    supported=True,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                # D3 — 1 row
                District(
                    name="Smalltown USD",
                    state="KS",
                    enrollment=7500,
                    tier="D3",
                    supported=True,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                # D4 — 4 rows (unsupported)
                District(
                    name="Tiny Valley USD",
                    state="WY",
                    enrollment=400,
                    tier="D4",
                    supported=False,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                District(
                    name="Micro District",
                    state="MT",
                    enrollment=200,
                    tier="D4",
                    supported=False,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                District(
                    name="Rural One",
                    state="ND",
                    enrollment=100,
                    tier="D4",
                    supported=False,
                    on_skip_list=False,
                    classification_source="nces",
                ),
                District(
                    name="Rural Two",
                    state="SD",
                    enrollment=50,
                    tier="D4",
                    supported=False,
                    on_skip_list=False,
                    classification_source="nces",
                ),
            ]
        )


async def _seed_meta(
    session: AsyncSession,
    *,
    loaded_at: datetime,
    school_year: str = "2024-25",
    row_count: int = 10,
) -> DistrictDataMeta:
    """Seed a district_data_meta row with an explicit loaded_at timestamp."""
    async with session.begin():
        meta = DistrictDataMeta(
            source="NCES CCD via Urban Institute Education Data API",
            school_year=school_year,
            loaded_at=loaded_at,
            row_count=row_count,
            updated_at=loaded_at,
        )
        session.add(meta)
    await session.refresh(meta)
    return meta


# ---------------------------------------------------------------------------
# Test 1 — Correct counts with stamped meta + seeded districts
# ---------------------------------------------------------------------------


async def test_district_data_status_correct_counts(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/signal-criteria/district-data-status returns accurate tier + support counts."""
    now = datetime.now(tz=UTC)
    await _seed_bands(db_session)
    await _seed_districts(db_session)
    await _seed_meta(db_session, loaded_at=now, school_year="2024-25", row_count=10)

    response = await client.get("/api/signal-criteria/district-data-status")

    assert response.status_code == 200
    body = response.json()

    assert body["loaded"] is True
    # Pydantic serializes as snake_case (model_config does not set alias_generator)
    assert body["school_year"] == "2024-25"
    assert body["source"] == "NCES CCD via Urban Institute Education Data API"

    # 10 total districts seeded (2 D1 + 3 D2 + 1 D3 + 4 D4)
    assert body["total_districts"] == 10

    assert body["supported_count"] == 6  # D1(2) + D2(3) + D3(1)
    assert body["unsupported_count"] == 4  # D4(4)

    tier_counts = body["tier_counts"]
    assert tier_counts is not None
    assert tier_counts.get("D1") == 2
    assert tier_counts.get("D2") == 3
    assert tier_counts.get("D3") == 1
    assert tier_counts.get("D4") == 4


# ---------------------------------------------------------------------------
# Test 2 — Freshness thresholds
# ---------------------------------------------------------------------------


async def test_freshness_current(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """loaded_at = now → freshness='current', months_since_loaded=0."""
    now = datetime.now(tz=UTC)
    await _seed_meta(db_session, loaded_at=now)

    response = await client.get("/api/signal-criteria/district-data-status")
    assert response.status_code == 200
    body = response.json()

    assert body["loaded"] is True
    assert body["freshness"] == "current"
    assert body["months_since_loaded"] == 0


async def test_freshness_aging(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """loaded_at = 14 months ago → freshness='aging'."""
    loaded_at = datetime.now(tz=UTC) - timedelta(days=14 * 31)
    await _seed_meta(db_session, loaded_at=loaded_at)

    response = await client.get("/api/signal-criteria/district-data-status")
    assert response.status_code == 200
    body = response.json()

    assert body["loaded"] is True
    assert body["freshness"] == "aging"
    assert body["months_since_loaded"] >= 14


async def test_freshness_stale(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """loaded_at = 20 months ago → freshness='stale'."""
    loaded_at = datetime.now(tz=UTC) - timedelta(days=20 * 31)
    await _seed_meta(db_session, loaded_at=loaded_at)

    response = await client.get("/api/signal-criteria/district-data-status")
    assert response.status_code == 200
    body = response.json()

    assert body["loaded"] is True
    assert body["freshness"] == "stale"
    assert body["months_since_loaded"] >= 18


# ---------------------------------------------------------------------------
# Test 3 — Empty district_data_meta → honest empty state
# ---------------------------------------------------------------------------


async def test_empty_meta_honest_empty_state(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
) -> None:
    """When district_data_meta has no row, endpoint returns loaded=False, no fabricated numbers."""
    response = await client.get("/api/signal-criteria/district-data-status")

    assert response.status_code == 200
    body = response.json()

    assert body["loaded"] is False
    # All numeric/data fields must be null — no fabricated counts
    assert body.get("total_districts") is None
    assert body.get("supported_count") is None
    assert body.get("tier_counts") is None
    assert body.get("freshness") is None
    assert body.get("school_year") is None


# ---------------------------------------------------------------------------
# Test 4 — Loader stamps district_data_meta on load
# ---------------------------------------------------------------------------

_MINI_CSV = """\
nces_id,name,state,enrollment
1001001,Alpha USD,CA,30000
1001002,Beta USD,TX,8000
1001003,Gamma USD,FL,3000
"""


async def test_district_data_refresh_endpoint_spawns_subprocess(
    client: AsyncClient,
    db_session: AsyncSession,  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /district-data-refresh returns 202 and dispatches the refresh CLI subprocess.

    Mocks asyncio.create_subprocess_exec so the test never touches the
    Urban Institute API.
    """
    import asyncio
    import sys as _sys

    from artemis.marketing import district_refresh_cli
    from artemis.marketing.routes import signal_criteria as sc_module

    # Reset single-flight state so this test isn't gated by an earlier run.
    sc_module._REFRESH_STATE["task"] = None
    sc_module._REFRESH_STATE["started_at"] = None

    captured: list[dict[str, object]] = []
    proc_done = asyncio.Event()

    class _FakeProc:
        returncode = 0
        pid = 12345

        async def communicate(self) -> tuple[bytes, bytes | None]:
            await proc_done.wait()
            return b'{"status": "ok", "loaded": 0, "recomputed": 0}\n', None

    async def _fake_create(*argv: str, **kwargs: object) -> _FakeProc:
        captured.append({"argv": list(argv), "kwargs": kwargs})
        return _FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)

    response = await client.post("/api/signal-criteria/district-data-refresh")
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "started"
    assert "started_at" in body

    # Give the event loop a tick to run the spawn-and-reap task.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(captured) == 1, "exactly one subprocess spawned per request"
    argv = captured[0]["argv"]
    assert argv[0] == _sys.executable
    assert argv[1] == "-m"
    assert argv[2] == district_refresh_cli.MODULE_NAME == "artemis.marketing.district_refresh_cli"
    assert captured[0]["kwargs"]["cwd"].endswith("artemis-os")

    # While the subprocess is still pretending to run, a second click must
    # 409 instead of spawning a duplicate.
    second = await client.post("/api/signal-criteria/district-data-refresh")
    assert second.status_code == 409, second.text
    assert len(captured) == 1, "second click must NOT spawn a duplicate subprocess"

    # Let the subprocess "finish" so the reaper task drains.
    proc_done.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # After completion the state is cleared and a new refresh can start.
    sc_module._REFRESH_STATE["task"] = None
    sc_module._REFRESH_STATE["started_at"] = None


async def test_loader_stamps_meta_on_load(
    db_session: AsyncSession,
) -> None:
    """load_districts_from_csv stamps district_data_meta with correct school_year + row_count."""
    import tempfile
    from pathlib import Path

    from artemis.marketing.nces_loader import load_districts_from_csv

    # Must seed bands first so tier classification doesn't fail
    async with db_session.begin():
        db_session.add_all(_default_bands())

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as tmp:
        tmp.write(_MINI_CSV)
        csv_path = Path(tmp.name)

    result = await load_districts_from_csv(
        db_session,
        csv_path,
        school_year="2024-25",
    )
    await db_session.commit()

    assert result["loaded"] == 3
    assert result["skipped"] == 0

    from sqlalchemy import select as sa_select

    meta_result = await db_session.execute(
        sa_select(DistrictDataMeta).order_by(DistrictDataMeta.id).limit(1)
    )
    meta = meta_result.scalar_one_or_none()

    assert meta is not None, "district_data_meta was not stamped by the loader"
    assert meta.school_year == "2024-25"
    assert meta.row_count == 3
    assert meta.source == "NCES CCD via Urban Institute Education Data API"
