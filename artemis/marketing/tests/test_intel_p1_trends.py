"""Tests for Phase 1 marketing intelligence — deterministic trend substrate.

Covers:
  - compute_momentum: time-series buckets + period-over-period delta
  - count_comparable_districts: filtering skip-listed / unsupported districts
  - compute_velocity_ranking: urgency-weighted scoring
  - compute_time_sensitivity: recency + urgency proxy (schema gap documented)
  - persist_trend_snapshot: memory_observations row + scope rows written
  - Determinism: functions return byte-equal results on identical inputs

Fixtures: clean_session wraps the marketing conftest db_session fixture and
additionally truncates memory tables in the SAME connection. This avoids the
deadlock that occurs when two separate connections both try to TRUNCATE
overlapping table sets simultaneously.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.models  # noqa: F401 — registers all marketing models on Base.metadata
import artemis.memory.models  # noqa: F401 — registers memory models on Base.metadata
import artemis.pipelines.models  # noqa: F401 — pipeline_runs FK dep
from artemis.marketing.intel.schemas import TrendSnapshot
from artemis.marketing.intel.trends import (
    compute_momentum,
    compute_time_sensitivity,
    compute_velocity_ranking,
    count_comparable_districts,
    persist_trend_snapshot,
)
from artemis.marketing.models import District, SignalQueue
from artemis.marketing.repository import create_signal
from artemis.memory.models import MemoryObservation, MemoryObservationScope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 4, 12, 0, 0, tzinfo=UTC)


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
    # Override signal_status and created_at after flush (create_signal always
    # sets signal_status via the model default; override here for test control)
    sig.signal_status = signal_status
    if created_at is not None:
        sig.created_at = created_at
    await session.flush()
    return sig


# ---------------------------------------------------------------------------
# Additional memory-table TRUNCATE — run in the SAME connection as db_session
# to avoid lock contention across connections.
# ---------------------------------------------------------------------------

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
    """Wrap the marketing conftest db_session and additionally truncate memory tables.

    Runs the memory TRUNCATE in the SAME session/connection as db_session so
    there is no cross-connection lock contention. The conftest already patched
    artemis.db.SessionLocal to the test engine at import time, so
    _multi_scope_observation_write also uses the test DB.

    db_session yields the session after committing its TRUNCATE, so there is
    no active transaction — we open one for the memory truncate, commit, then
    yield for test use (tests call session.commit() themselves as needed).
    """
    async with db_session.begin():
        await db_session.execute(_TRUNCATE_MEMORY)
    yield db_session


# ---------------------------------------------------------------------------
# 1. Momentum
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_momentum_buckets_and_delta(clean_session: AsyncSession) -> None:
    """Seed 6 signals in current quarter + 2 in prior quarter; assert buckets + delta ≈ 3.0."""
    district = await _make_district(clean_session, name="Momentum ISD")
    did = district.id

    # Current window: last 90 days — 6 signals spread across 3 weeks
    for i in range(3):
        await _make_signal(
            clean_session,
            headline=f"Current W1 signal {i}",
            resolved_district_id=did,
            created_at=_NOW - timedelta(days=7 + i),  # week 1 bucket
        )
    for i in range(3):
        await _make_signal(
            clean_session,
            headline=f"Current W2 signal {i}",
            resolved_district_id=did,
            created_at=_NOW - timedelta(days=20 + i),  # week 2/3 bucket
        )

    # Prior window: signals 90–180 days ago — 2 signals
    await _make_signal(
        clean_session,
        headline="Prior 1",
        resolved_district_id=did,
        created_at=_NOW - timedelta(days=100),
    )
    await _make_signal(
        clean_session,
        headline="Prior 2",
        resolved_district_id=did,
        created_at=_NOW - timedelta(days=120),
    )

    await clean_session.commit()

    result = await compute_momentum(
        clean_session,
        theme="obc",
        region="TX",
        as_of=_NOW,
        window_days=90,
        bucket_days=7,
    )

    assert result.theme == "obc"
    assert result.region == "TX"
    assert result.current_window_count == 6
    assert result.prior_window_count == 2
    assert result.delta_ratio is not None
    assert abs(result.delta_ratio - 3.0) < 0.01
    # Buckets exist and sum correctly
    assert len(result.buckets) >= 2
    assert sum(b.count for b in result.buckets) == 8  # 6 current + 2 prior


@pytest.mark.asyncio
async def test_momentum_no_prior_signals(clean_session: AsyncSession) -> None:
    """No prior-window signals → delta_ratio should be None."""
    district = await _make_district(clean_session, name="Fresh ISD")
    await _make_signal(
        clean_session,
        headline="New signal",
        resolved_district_id=district.id,
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    result = await compute_momentum(
        clean_session,
        theme="obc",
        region="TX",
        as_of=_NOW,
        window_days=90,
        bucket_days=7,
    )
    assert result.delta_ratio is None
    assert result.current_window_count == 1
    assert result.prior_window_count == 0


@pytest.mark.asyncio
async def test_momentum_excludes_non_active_statuses(clean_session: AsyncSession) -> None:
    """pending_qualification signals should NOT be counted in momentum."""
    district = await _make_district(clean_session, name="Pending ISD")
    await _make_signal(
        clean_session,
        headline="Pending signal",
        resolved_district_id=district.id,
        signal_status="pending_qualification",
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    result = await compute_momentum(clean_session, theme="obc", region="TX", as_of=_NOW)
    assert result.current_window_count == 0


# ---------------------------------------------------------------------------
# 2. Comparable districts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_comparables_excludes_skip_and_unsupported(clean_session: AsyncSession) -> None:
    """5 valid districts + 1 skip-listed + 1 unsupported → only 5 returned."""
    valid_ids = []
    for i in range(5):
        d = await _make_district(clean_session, name=f"Valid ISD {i}")
        valid_ids.append(d.id)
        await _make_signal(
            clean_session,
            headline=f"Valid signal {i}",
            resolved_district_id=d.id,
            created_at=_NOW - timedelta(days=10 + i),
        )

    skip_d = await _make_district(clean_session, name="Skip ISD", on_skip_list=True)
    await _make_signal(
        clean_session,
        headline="Skip signal",
        resolved_district_id=skip_d.id,
        created_at=_NOW - timedelta(days=5),
    )

    unsup_d = await _make_district(clean_session, name="Unsupported ISD", supported=False)
    await _make_signal(
        clean_session,
        headline="Unsupported signal",
        resolved_district_id=unsup_d.id,
        created_at=_NOW - timedelta(days=5),
    )

    await clean_session.commit()

    result = await count_comparable_districts(
        clean_session,
        theme="obc",
        region="TX",
        as_of=_NOW,
        window_days=90,
    )

    assert result.comparable_count == 5
    returned_ids = {d.district_id for d in result.sample_districts}
    # None of the returned districts should be the skip or unsupported ones
    assert skip_d.id not in returned_ids
    assert unsup_d.id not in returned_ids
    # All valid IDs are represented (5 ≤ sample limit of 10)
    for vid in valid_ids:
        assert vid in returned_ids


@pytest.mark.asyncio
async def test_comparables_region_filter(clean_session: AsyncSession) -> None:
    """Signals from CA should not appear when region='TX'."""
    tx_d = await _make_district(clean_session, name="TX ISD", state="TX")
    ca_d = await _make_district(clean_session, name="CA ISD", state="CA")
    await _make_signal(
        clean_session,
        headline="TX signal",
        resolved_district_id=tx_d.id,
        state="TX",
        created_at=_NOW - timedelta(days=10),
    )
    await _make_signal(
        clean_session,
        headline="CA signal",
        resolved_district_id=ca_d.id,
        state="CA",
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    result = await count_comparable_districts(clean_session, theme="obc", region="TX", as_of=_NOW)
    assert result.comparable_count == 1
    assert result.sample_districts[0].state == "TX"


# ---------------------------------------------------------------------------
# 3. Velocity ranking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_velocity_ranking_urgency_beats_raw_count(clean_session: AsyncSession) -> None:
    """A district with 1 'critical' signal should outscore a district with 3 'standard' signals.

    1 critical = 5 weighted; 3 standard = 3 weighted → critical wins.
    """
    d_critical = await _make_district(clean_session, name="Critical ISD")
    d_standard = await _make_district(clean_session, name="Standard ISD")

    # d_critical: 1 critical signal → weighted_score=5.0
    await _make_signal(
        clean_session,
        headline="Critical hit",
        resolved_district_id=d_critical.id,
        urgency_tier="critical",
        created_at=_NOW - timedelta(days=5),
    )

    # d_standard: 3 standard signals → weighted_score=3.0
    for i in range(3):
        await _make_signal(
            clean_session,
            headline=f"Standard signal {i}",
            resolved_district_id=d_standard.id,
            urgency_tier="standard",
            created_at=_NOW - timedelta(days=5 + i),
        )

    await clean_session.commit()

    ranking = await compute_velocity_ranking(clean_session, as_of=_NOW, window_days=30)

    assert len(ranking) >= 2
    # rank 1 should be the critical ISD
    assert ranking[0].district.district_id == d_critical.id
    assert ranking[0].rank == 1
    assert ranking[0].weighted_score == pytest.approx(5.0)
    # rank 2 should be the standard ISD
    assert ranking[1].district.district_id == d_standard.id
    assert ranking[1].weighted_score == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_velocity_ranking_urgency_mix(clean_session: AsyncSession) -> None:
    """urgency_mix breakdown should reflect per-tier counts."""
    d = await _make_district(clean_session, name="Mix ISD")
    for tier, count in [("standard", 2), ("elevated", 1), ("high", 1), ("critical", 1)]:
        for i in range(count):
            await _make_signal(
                clean_session,
                headline=f"{tier} signal {i}",
                resolved_district_id=d.id,
                urgency_tier=tier,
                created_at=_NOW - timedelta(days=5),
            )
    await clean_session.commit()

    ranking = await compute_velocity_ranking(clean_session, as_of=_NOW, window_days=30)
    assert len(ranking) == 1
    mix = ranking[0].urgency_mix
    assert mix.standard == 2
    assert mix.elevated == 1
    assert mix.high == 1
    assert mix.critical == 1
    expected_score = 2 * 1.0 + 1 * 2.0 + 1 * 3.0 + 1 * 5.0
    assert ranking[0].weighted_score == pytest.approx(expected_score)


# ---------------------------------------------------------------------------
# 4. Time sensitivity (schema-gap proxy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_time_sensitivity_near_term_appears(clean_session: AsyncSession) -> None:
    """Signals within horizon_days appear; older signals do not."""
    d = await _make_district(clean_session, name="Time ISD")

    # Near-term: 30 days ago (within 60d horizon)
    await _make_signal(
        clean_session,
        headline="Near-term signal",
        resolved_district_id=d.id,
        urgency_tier="high",
        created_at=_NOW - timedelta(days=30),
    )

    # Far: 90 days ago (outside 60d horizon)
    await _make_signal(
        clean_session,
        headline="Far-future signal",
        resolved_district_id=d.id,
        urgency_tier="standard",
        created_at=_NOW - timedelta(days=90),
    )

    await clean_session.commit()

    results = await compute_time_sensitivity(clean_session, as_of=_NOW, horizon_days=60)

    headlines = [r.headline for r in results]
    assert "Near-term signal" in headlines
    assert "Far-future signal" not in headlines


@pytest.mark.asyncio
async def test_time_sensitivity_urgency_ordering(clean_session: AsyncSession) -> None:
    """critical signals should appear before standard ones in the results."""
    d = await _make_district(clean_session, name="Urgency Order ISD")

    await _make_signal(
        clean_session,
        headline="Standard recent",
        resolved_district_id=d.id,
        urgency_tier="standard",
        created_at=_NOW - timedelta(days=5),
    )
    await _make_signal(
        clean_session,
        headline="Critical older",
        resolved_district_id=d.id,
        urgency_tier="critical",
        created_at=_NOW - timedelta(days=50),
    )

    await clean_session.commit()

    results = await compute_time_sensitivity(clean_session, as_of=_NOW, horizon_days=60, limit=10)
    assert len(results) >= 2
    # critical should sort first
    assert results[0].urgency_tier == "critical"


@pytest.mark.asyncio
async def test_time_sensitivity_deadline_source_is_proxy(clean_session: AsyncSession) -> None:
    """deadline_source should document the schema-gap proxy label."""
    d = await _make_district(clean_session, name="Deadline ISD")
    await _make_signal(
        clean_session,
        headline="Proxy deadline signal",
        resolved_district_id=d.id,
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    results = await compute_time_sensitivity(clean_session, as_of=_NOW, horizon_days=60)
    assert len(results) == 1
    assert results[0].deadline_source == "created_at_urgency_proxy"


# ---------------------------------------------------------------------------
# 5. Persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persist_trend_snapshot_writes_observation(clean_session: AsyncSession) -> None:
    """persist_trend_snapshot should write a row in memory_observations with the right category."""
    snapshot = TrendSnapshot(
        as_of=_NOW,
        theme="obc",
        region="TX",
        snapshot_kind="momentum",
        content_summary="TX obc momentum: 6 signals this quarter vs 2 prior (3.0×)",
        payload={"current_window_count": 6, "prior_window_count": 2, "delta_ratio": 3.0},
    )

    obs_id = await persist_trend_snapshot(
        clean_session,
        snapshot=snapshot,
        primary_scope_kind="workspace",
        primary_scope_id="marketing",
        additional_scopes=[("workspace", "platform")],
    )

    assert isinstance(obs_id, int)
    assert obs_id > 0

    # Verify the observation row exists with correct category
    row = await clean_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_id)
    )
    obs = row.scalar_one()
    assert obs.category == "trend_snapshot"
    assert obs.source_quality == pytest.approx(0.85)
    assert "TX obc momentum" in obs.content
    assert obs.confidence_origin == "deterministic_aggregation"

    # Verify the primary scope row in memory_observation_scopes
    scopes_rows = (
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
    scope_keys = {(s.scope_kind, s.scope_id) for s in scopes_rows}
    assert ("workspace", "marketing") in scope_keys
    assert ("workspace", "platform") in scope_keys

    # Verify is_primary flag on the primary scope row
    primary_rows = [s for s in scopes_rows if s.is_primary]
    assert len(primary_rows) == 1
    assert primary_rows[0].scope_kind == "workspace"
    assert primary_rows[0].scope_id == "marketing"


@pytest.mark.asyncio
async def test_persist_trend_snapshot_payload_in_content(clean_session: AsyncSession) -> None:
    """Snapshot payload should be embedded in observation content (FTS/semantic)."""
    snapshot = TrendSnapshot(
        as_of=_NOW,
        theme="obc",
        region=None,
        snapshot_kind="velocity",
        content_summary="Nationwide velocity ranking as of 2026-06-04",
        payload={"top_district": "Momentum ISD", "score": 12.5},
    )

    obs_id = await persist_trend_snapshot(
        clean_session,
        snapshot=snapshot,
        primary_scope_kind="workspace",
        primary_scope_id="marketing",
        additional_scopes=[],
    )

    row = await clean_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_id)
    )
    obs = row.scalar_one()
    # Content should contain both the summary and the JSON payload
    assert "Nationwide velocity ranking" in obs.content
    assert "Momentum ISD" in obs.content


# ---------------------------------------------------------------------------
# 6. Determinism
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_determinism_momentum(clean_session: AsyncSession) -> None:
    """compute_momentum with identical inputs returns byte-equal results."""
    district = await _make_district(clean_session, name="Determinism ISD")
    await _make_signal(
        clean_session,
        headline="Determinism signal",
        resolved_district_id=district.id,
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    r1 = await compute_momentum(clean_session, theme="obc", region="TX", as_of=_NOW)
    r2 = await compute_momentum(clean_session, theme="obc", region="TX", as_of=_NOW)
    assert r1 == r2


@pytest.mark.asyncio
async def test_determinism_comparables(clean_session: AsyncSession) -> None:
    """count_comparable_districts with identical inputs returns byte-equal results."""
    district = await _make_district(clean_session, name="Det Comparables ISD")
    await _make_signal(
        clean_session,
        headline="Det comp signal",
        resolved_district_id=district.id,
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    r1 = await count_comparable_districts(clean_session, theme="obc", region="TX", as_of=_NOW)
    r2 = await count_comparable_districts(clean_session, theme="obc", region="TX", as_of=_NOW)
    assert r1 == r2


@pytest.mark.asyncio
async def test_determinism_velocity(clean_session: AsyncSession) -> None:
    """compute_velocity_ranking with identical inputs returns byte-equal results."""
    district = await _make_district(clean_session, name="Det Velocity ISD")
    await _make_signal(
        clean_session,
        headline="Det vel signal",
        resolved_district_id=district.id,
        created_at=_NOW - timedelta(days=5),
    )
    await clean_session.commit()

    r1 = await compute_velocity_ranking(clean_session, as_of=_NOW, window_days=30)
    r2 = await compute_velocity_ranking(clean_session, as_of=_NOW, window_days=30)
    assert r1 == r2


@pytest.mark.asyncio
async def test_determinism_time_sensitivity(clean_session: AsyncSession) -> None:
    """compute_time_sensitivity with identical inputs returns byte-equal results."""
    district = await _make_district(clean_session, name="Det TimeSens ISD")
    await _make_signal(
        clean_session,
        headline="Det ts signal",
        resolved_district_id=district.id,
        created_at=_NOW - timedelta(days=10),
    )
    await clean_session.commit()

    r1 = await compute_time_sensitivity(clean_session, as_of=_NOW, horizon_days=60)
    r2 = await compute_time_sensitivity(clean_session, as_of=_NOW, horizon_days=60)
    assert r1 == r2
