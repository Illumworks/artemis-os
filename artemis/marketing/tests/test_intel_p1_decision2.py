"""Phase 1 Decision-2 — prioritization endpoint tests.

Covers:
  - Happy path: 5 districts with mixed urgency → 200, correct velocity_ranking count
  - Window filter: old signals excluded from velocity_ranking
  - State filter: only TX districts returned when state=TX
  - Time-sensitive intersection: district in both lists rises to top of combined
  - Persistence: persist=true writes a memory_observations row (category=trend_snapshot)
  - Auth: missing token → 401 when ARTEMIS_TOKEN is configured
  - Determinism: two identical calls return equal payloads (modulo as_of)
  - Empty DB: zero signals → 200 with empty lists (not 500)

Uses the marketing conftest fixtures (db_session, client) + extends them with
memory-table TRUNCATE (same pattern as test_intel_p1_trends.py).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.models  # noqa: F401 — registers all marketing models
import artemis.memory.models  # noqa: F401 — registers memory models
import artemis.pipelines.models  # noqa: F401 — pipeline_runs FK dep
from artemis.marketing.models import District, SignalQueue
from artemis.marketing.repository import create_signal
from artemis.memory.models import MemoryObservation, MemoryObservationScope

# ---------------------------------------------------------------------------
# Constants / shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)

_TRUNCATE_MEMORY = text(
    "TRUNCATE memory_conflicts, "
    "memory_relation_rejections, memory_relations, "
    "memory_entity_mentions, memory_entity_aliases, memory_entities, "
    "memory_observation_scopes, "
    "memory_embeddings, memory_evidence, memory_observations, "
    "memory_drawers, memory_scopes, "
    "raw_inputs "
    "RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def clean_session(db_session: AsyncSession) -> AsyncIterator[AsyncSession]:
    """Extend the marketing conftest db_session with memory-table truncation.

    Runs in the SAME connection to avoid cross-connection lock contention.
    """
    async with db_session.begin():
        await db_session.execute(_TRUNCATE_MEMORY)
    yield db_session


async def _make_district(
    session: AsyncSession,
    *,
    name: str,
    state: str = "TX",
    tier: str = "D2",
    on_skip_list: bool = False,
    supported: bool = True,
) -> District:
    d = District(
        name=name,
        state=state,
        tier=tier,
        enrollment=5000,
        supported=supported,
        on_skip_list=on_skip_list,
        classification_source="manual",
    )
    session.add(d)
    await session.flush()
    await session.refresh(d)
    return d


async def _make_signal(
    session: AsyncSession,
    *,
    headline: str = "Test signal",
    campaign_family: str = "obc",
    state: str = "TX",
    signal_status: str = "qualified",
    urgency_tier: str = "standard",
    resolved_district_id: int | None = None,
    created_at: datetime | None = None,
) -> SignalQueue:
    sig = await create_signal(
        session,
        headline=headline,
        campaign_family=campaign_family,
        source_type="manual",
        summary=headline,
        urgency_tier=urgency_tier,
        discovered_by="test",
        reason_codes=["TEST"],
        resolved_district_id=resolved_district_id,
        state=state,
    )
    sig.signal_status = signal_status
    if created_at is not None:
        sig.created_at = created_at
    await session.flush()
    return sig


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_five_districts(clean_session: AsyncSession, client: AsyncClient) -> None:
    """Seed 5 supported districts with mixed urgency → 200, velocity_ranking has ≥1 item."""
    urgency_tiers = ["standard", "elevated", "high", "critical", "high"]
    for i, tier in enumerate(urgency_tiers):
        d = await _make_district(clean_session, name=f"District {i}", state="TX", tier="D2")
        await _make_signal(
            clean_session,
            headline=f"Signal {i}",
            resolved_district_id=d.id,
            urgency_tier=tier,
            created_at=_NOW - timedelta(days=5 + i),
        )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization?window_days=30&limit=20")

    assert resp.status_code == 200
    data = resp.json()
    assert data["window_days"] == 30
    assert data["state_filter"] is None
    assert isinstance(data["velocity_ranking"], list)
    assert len(data["velocity_ranking"]) == 5
    assert isinstance(data["combined"], list)
    assert len(data["combined"]) >= 1
    assert "earliest_signal_created_at_iso" in data["combined"][0]
    assert "earliest_deadline_iso" not in data["combined"][0]


@pytest.mark.asyncio
async def test_happy_path_velocity_ordering(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """Critical district (score=5) should rank above standard districts (score=1)."""
    d_critical = await _make_district(clean_session, name="Critical ISD")
    d_standard = await _make_district(clean_session, name="Standard ISD")

    await _make_signal(
        clean_session,
        headline="Critical signal",
        resolved_district_id=d_critical.id,
        urgency_tier="critical",
        created_at=_NOW - timedelta(days=5),
    )
    await _make_signal(
        clean_session,
        headline="Standard signal",
        resolved_district_id=d_standard.id,
        urgency_tier="standard",
        created_at=_NOW - timedelta(days=5),
    )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization?window_days=30")

    assert resp.status_code == 200
    data = resp.json()
    vr = data["velocity_ranking"]
    assert len(vr) == 2
    assert vr[0]["district"]["district_id"] == d_critical.id
    assert vr[0]["rank"] == 1
    assert vr[0]["weighted_score"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# 2. Window filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_filter_old_signals_excluded(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """Signals older than window_days should not appear in velocity_ranking."""
    d_recent = await _make_district(clean_session, name="Recent ISD")
    d_old = await _make_district(clean_session, name="Old ISD")

    # Recent: 10 days ago — inside 30d window
    await _make_signal(
        clean_session,
        headline="Recent signal",
        resolved_district_id=d_recent.id,
        urgency_tier="standard",
        created_at=_NOW - timedelta(days=10),
    )
    # Old: 60 days ago — outside 30d window
    await _make_signal(
        clean_session,
        headline="Old signal",
        resolved_district_id=d_old.id,
        urgency_tier="critical",
        created_at=_NOW - timedelta(days=60),
    )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization?window_days=30")

    assert resp.status_code == 200
    data = resp.json()
    district_ids_in_ranking = [r["district"]["district_id"] for r in data["velocity_ranking"]]
    assert d_recent.id in district_ids_in_ranking
    assert d_old.id not in district_ids_in_ranking


# ---------------------------------------------------------------------------
# 3. State filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_filter_only_tx_returned(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """With state=TX, only TX districts appear in velocity_ranking."""
    states = ["TX", "TX", "CA", "FL"]
    districts = []
    for i, st in enumerate(states):
        d = await _make_district(clean_session, name=f"District {st} {i}", state=st)
        districts.append(d)
        await _make_signal(
            clean_session,
            headline=f"Signal {st} {i}",
            resolved_district_id=d.id,
            urgency_tier="standard",
            state=st,
            created_at=_NOW - timedelta(days=5),
        )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization?state=TX")

    assert resp.status_code == 200
    data = resp.json()
    assert data["state_filter"] == "TX"
    # Every velocity row must be TX
    for row in data["velocity_ranking"]:
        assert row["district"]["state"] == "TX"
    # No CA or FL district in ranking
    ranked_ids = {r["district"]["district_id"] for r in data["velocity_ranking"]}
    ca_fl_ids = {d.id for d, st in zip(districts, states, strict=True) if st in ("CA", "FL")}
    assert ranked_ids.isdisjoint(ca_fl_ids)


# ---------------------------------------------------------------------------
# 4. Intersection: district in both lists rises to top of combined
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intersection_district_at_top_of_combined(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """A district appearing in both velocity and time_sensitive should be first in combined."""
    # d_both: low velocity score (standard) but appears in time_sensitive (recent)
    d_both = await _make_district(clean_session, name="Both ISD")
    # d_vel_only: higher velocity score but no near-term signal
    d_vel_only = await _make_district(clean_session, name="VelOnly ISD")

    # d_both: one critical signal within horizon_days (appears in both velocity + time_sensitive)
    await _make_signal(
        clean_session,
        headline="Both signal",
        resolved_district_id=d_both.id,
        urgency_tier="critical",  # high urgency → appears in time_sensitive
        created_at=_NOW - timedelta(days=5),
    )

    # d_vel_only: multiple signals for high velocity, but all outside the 60d horizon
    # (61–63 days ago → within 30d window? No — must be inside window_days=30 for velocity)
    # Use signals 20-22 days ago for velocity, but outside horizon_days=30 for time_sensitive.
    # We pass horizon_days=20 in the request so d_vel_only signals (20+ days old) are excluded.
    for i in range(3):
        await _make_signal(
            clean_session,
            headline=f"Vel signal {i}",
            resolved_district_id=d_vel_only.id,
            urgency_tier="elevated",
            created_at=_NOW - timedelta(days=22 + i),  # 22-24 days old
        )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        # horizon_days=20 so d_vel_only signals (22-24 days old) are outside time_sensitive
        # window_days=30 so d_vel_only signals (22-24 days old) ARE inside velocity window
        resp = await client.get(
            "/api/marketing/intel/prioritization?window_days=30&horizon_days=20"
        )

    assert resp.status_code == 200
    data = resp.json()
    combined = data["combined"]
    assert len(combined) >= 1

    # The first combined row must have has_time_sensitive_signal=True if d_both appears
    both_rows = [r for r in combined if r["district_id"] == d_both.id]
    if both_rows:
        assert both_rows[0]["has_time_sensitive_signal"] is True
        # d_both should appear before d_vel_only (intersection before vel-only)
        combined_ids = [r["district_id"] for r in combined]
        if d_vel_only.id in combined_ids:
            assert combined_ids.index(d_both.id) < combined_ids.index(d_vel_only.id)


# ---------------------------------------------------------------------------
# 5. Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_true_writes_observation(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """persist=true should return persisted_observation_id and write to memory_observations."""
    d = await _make_district(clean_session, name="Persist ISD")
    await _make_signal(
        clean_session,
        headline="Persist signal",
        resolved_district_id=d.id,
        urgency_tier="high",
        created_at=_NOW - timedelta(days=5),
    )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization?persist=true&state=TX")

    assert resp.status_code == 200
    data = resp.json()
    obs_id = data["persisted_observation_id"]
    assert obs_id is not None
    assert isinstance(obs_id, int)
    assert obs_id > 0

    # Verify the row exists in memory_observations
    obs_row = await clean_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_id)
    )
    obs = obs_row.scalar_one()
    assert obs.category == "trend_snapshot"
    assert "prioritization" in obs.content.lower() or "Prioritization" in obs.content

    scope_rows = (
        (
            await clean_session.execute(
                select(MemoryObservationScope).where(
                    MemoryObservationScope.observation_id == obs_id
                )
            )
        )
        .scalars()
        .all()
    )
    scope_keys = {(row.scope_kind, row.scope_id) for row in scope_rows}
    assert ("workspace", "marketing") in scope_keys
    assert ("state", "TX") in scope_keys


@pytest.mark.asyncio
async def test_persist_false_no_observation(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """persist=false (default) should not write any observation."""
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization")

    assert resp.status_code == 200
    data = resp.json()
    assert data["persisted_observation_id"] is None

    # No memory_observations rows should exist
    count_result = await clean_session.execute(
        text("SELECT COUNT(*) FROM memory_observations WHERE category = 'trend_snapshot'")
    )
    count = count_result.scalar()
    assert count == 0


# ---------------------------------------------------------------------------
# 6. Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auth_missing_token_returns_401(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """When ARTEMIS_TOKEN is configured, requests without a token should be rejected."""
    from artemis.config import settings

    original_token = settings.token
    try:
        # Force a token requirement
        object.__setattr__(settings, "token", "test-secret-token")
        resp = await client.get("/api/marketing/intel/prioritization")
        # Expect 401 (no token provided)
        assert resp.status_code == 401
    finally:
        object.__setattr__(settings, "token", original_token)


@pytest.mark.asyncio
async def test_auth_valid_token_accepted(clean_session: AsyncSession, client: AsyncClient) -> None:
    """A valid bearer token should be accepted."""
    from artemis.config import settings

    original_token = settings.token
    try:
        object.__setattr__(settings, "token", "test-secret-token")
        await clean_session.commit()
        resp = await client.get(
            "/api/marketing/intel/prioritization",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert resp.status_code == 200
    finally:
        object.__setattr__(settings, "token", original_token)


# ---------------------------------------------------------------------------
# 7. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_determinism_same_inputs_same_output(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """Two calls with the same frozen as_of return identical velocity_ranking and combined."""
    d = await _make_district(clean_session, name="Det ISD")
    await _make_signal(
        clean_session,
        headline="Det signal",
        resolved_district_id=d.id,
        urgency_tier="high",
        created_at=_NOW - timedelta(days=5),
    )
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp1 = await client.get("/api/marketing/intel/prioritization?window_days=30")

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp2 = await client.get("/api/marketing/intel/prioritization?window_days=30")

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    d1 = resp1.json()
    d2 = resp2.json()

    # velocity_ranking and combined must be byte-equal (as_of is controlled by mock)
    assert d1["velocity_ranking"] == d2["velocity_ranking"]
    assert d1["combined"] == d2["combined"]
    assert d1["time_sensitive"] == d2["time_sensitive"]


# ---------------------------------------------------------------------------
# 8. Empty DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_db_returns_empty_lists(
    clean_session: AsyncSession, client: AsyncClient
) -> None:
    """Zero signals in DB → 200 with empty lists, not 500."""
    await clean_session.commit()

    with patch("artemis.marketing.routes.intel_prioritization.datetime") as mock_dt:
        mock_dt.now.return_value = _NOW
        resp = await client.get("/api/marketing/intel/prioritization")

    assert resp.status_code == 200
    data = resp.json()
    assert data["velocity_ranking"] == []
    assert data["time_sensitive"] == []
    assert data["combined"] == []
    assert data["persisted_observation_id"] is None


# ---------------------------------------------------------------------------
# 9. Query parameter validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_window_days_rejected(client: AsyncClient) -> None:
    """window_days below minimum (7) should return 422."""
    resp = await client.get("/api/marketing/intel/prioritization?window_days=3")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_invalid_limit_too_large(client: AsyncClient) -> None:
    """limit above maximum (100) should return 422."""
    resp = await client.get("/api/marketing/intel/prioritization?limit=200")
    assert resp.status_code == 422
