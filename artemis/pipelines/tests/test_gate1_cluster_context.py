"""Tests for cluster grouping + suggested scoring in the Gate-1 approval card.

Covers:
1. Clusters payload — 2 districts yield 2 cluster entries, exactly one suggested.
2. Suggested tiebreak — equal scores: more signals wins; equal signals: alpha key wins.
3. Score + reason fields populated.

Worker A test DB: artemis_test_worker_a
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.models  # noqa: F401 — register SignalQueue/District on Base.metadata
from artemis.marketing.models import District, SignalQueue
from artemis.pipelines import repository as repo
from artemis.pipelines.node_executors.human_gate_executor import (
    _build_signal_gate_context_from_db,
    _cluster_score,
)

pytestmark = pytest.mark.asyncio


# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_run(session: AsyncSession) -> str:
    pipeline = await repo.create_pipeline(
        session,
        name="Cluster Test Pipeline",
        nodes=[
            {
                "id": "gate_1",
                "type": "human_gate",
                "label": "Gate 1",
                "config": {"approval_kind": "signal_brief"},
                "position": {"x": 0.0, "y": 0.0},
            }
        ],
        edges=[],
    )
    run = await repo.create_pipeline_run(
        session,
        pipeline_id=pipeline.id,
        status="awaiting_approval",
        trigger="manual",
        triggered_by="test",
    )
    return run.id


async def _seed_district(session: AsyncSession, name: str, state: str) -> int:
    district = District(
        nces_id=f"NCES-{name[:4].upper()}",
        name=name,
        state=state,
        enrollment=5000,
        tier="D2",
        supported=True,
        on_skip_list=False,
        classification_source="manual",
        classified_at=datetime.now(UTC),
    )
    session.add(district)
    await session.flush()
    await session.refresh(district)
    return district.id


def _make_signal(
    run_id: str,
    *,
    headline: str,
    campaign_family: str = "marketing",
    resolved_district_id: int | None = None,
    district_id: str | None = None,
    fit_score: float | None = None,
    captured_at: str | None = None,
) -> SignalQueue:
    qual: dict[str, Any] | None = None
    if fit_score is not None or captured_at is not None:
        qual = {}
        if fit_score is not None:
            qual["fit_score"] = fit_score
        if captured_at is not None:
            qual["captured_at"] = captured_at
    return SignalQueue(
        source_type="scout",
        pipeline_run_id=run_id,
        headline=headline,
        summary="",
        campaign_family=campaign_family,
        urgency_tier="standard",
        discovered_by="test",
        resolved_district_id=resolved_district_id,
        district_id=district_id,
        reason_codes=[],
        qualification_json=qual,
        signal_status="qualified",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_clusters_two_districts_two_entries(db_session: AsyncSession) -> None:
    """Signals from 2 districts → 2 cluster entries, exactly one has suggested=True."""
    run_id = await _seed_run(db_session)
    dist_a = await _seed_district(db_session, "Alpha Unified", "CA")
    dist_b = await _seed_district(db_session, "Beta Unified", "TX")

    db_session.add_all(
        [
            _make_signal(run_id, headline="Signal A1", resolved_district_id=dist_a, fit_score=0.8),
            _make_signal(run_id, headline="Signal B1", resolved_district_id=dist_b, fit_score=0.6),
        ]
    )
    await db_session.flush()

    ctx = await _build_signal_gate_context_from_db("signal_brief", db_session, run_id)

    clusters = ctx["clusters"]
    assert len(clusters) == 2, f"Expected 2 clusters, got {len(clusters)}: {clusters}"

    suggested = [c for c in clusters if c["suggested"]]
    assert len(suggested) == 1, "Exactly one cluster must be suggested"

    # Backward compat: flat fields still present
    assert ctx["signal_count"] == 2
    assert "districts" in ctx
    assert "reason_codes" in ctx


async def test_cluster_signal_grouping_and_roles(db_session: AsyncSession) -> None:
    """Two signals in the same district+family → one cluster with primary + corroborating roles."""
    run_id = await _seed_run(db_session)
    dist_a = await _seed_district(db_session, "Gamma Unified", "OR")

    db_session.add_all(
        [
            _make_signal(run_id, headline="Primary", resolved_district_id=dist_a, fit_score=0.85),
            _make_signal(
                run_id, headline="Corroborating", resolved_district_id=dist_a, fit_score=0.60
            ),
        ]
    )
    await db_session.flush()

    ctx = await _build_signal_gate_context_from_db("signal_brief", db_session, run_id)

    assert len(ctx["clusters"]) == 1
    cluster = ctx["clusters"][0]
    assert len(cluster["signals"]) == 2
    roles = {s["role"] for s in cluster["signals"]}
    assert "primary" in roles
    assert "corroborating" in roles
    assert cluster["score"] > 0.0
    assert cluster["score_reason"]
    assert cluster["suggested"] is True


async def test_cluster_score_fields_populated(db_session: AsyncSession) -> None:
    """Cluster score and score_reason are non-empty."""
    run_id = await _seed_run(db_session)
    dist_a = await _seed_district(db_session, "Delta Unified", "NY")

    db_session.add(
        _make_signal(run_id, headline="Delta signal", resolved_district_id=dist_a, fit_score=0.7)
    )
    await db_session.flush()

    ctx = await _build_signal_gate_context_from_db("signal_brief", db_session, run_id)

    cluster = ctx["clusters"][0]
    assert isinstance(cluster["score"], float)
    assert 0.0 <= cluster["score"] <= 1.0
    assert isinstance(cluster["score_reason"], str)
    assert len(cluster["score_reason"]) > 0


async def test_cluster_empty_run_yields_no_clusters(db_session: AsyncSession) -> None:
    """A run with no qualified signals yields clusters=[]."""
    run_id = await _seed_run(db_session)

    ctx = await _build_signal_gate_context_from_db("signal_brief", db_session, run_id)

    assert ctx["clusters"] == []


# ── Unit tests for _cluster_score and tiebreak ────────────────────────────────


async def test_suggested_tiebreak_more_signals_wins(db_session: AsyncSession) -> None:
    """Two clusters with equal base scores: the one with more signals wins suggested."""
    run_id = await _seed_run(db_session)
    dist_a = await _seed_district(db_session, "Epsilon Unified", "WA")
    dist_b = await _seed_district(db_session, "Zeta Unified", "WA")

    # Both clusters get fit_score=0.6, no recency. dist_a gets 2 signals, dist_b gets 1.
    db_session.add_all(
        [
            _make_signal(run_id, headline="A1", resolved_district_id=dist_a, fit_score=0.6),
            _make_signal(run_id, headline="A2", resolved_district_id=dist_a, fit_score=0.6),
            _make_signal(run_id, headline="B1", resolved_district_id=dist_b, fit_score=0.6),
        ]
    )
    await db_session.flush()

    ctx = await _build_signal_gate_context_from_db("signal_brief", db_session, run_id)
    clusters = ctx["clusters"]

    assert len(clusters) == 2
    suggested = next(c for c in clusters if c["suggested"])
    # dist_a cluster has 2 signals → higher stacking bonus → higher score → suggested
    assert len(suggested["signals"]) == 2


async def test_suggested_tiebreak_alpha_cluster_key(db_session: AsyncSession) -> None:
    """Equal score AND equal signal count → lowest cluster_key alphabetically wins."""
    run_id = await _seed_run(db_session)
    dist_a = await _seed_district(db_session, "Alpha Zeta", "ZZ")
    dist_b = await _seed_district(db_session, "Beta Zeta", "ZZ")

    # Make dist_a have a lower ID (it was inserted first), so its cluster_key string is
    # f"{dist_a}|marketing" which is lexicographically before f"{dist_b}|marketing".
    db_session.add_all(
        [
            _make_signal(run_id, headline="A", resolved_district_id=dist_a, fit_score=0.5),
            _make_signal(run_id, headline="B", resolved_district_id=dist_b, fit_score=0.5),
        ]
    )
    await db_session.flush()

    ctx = await _build_signal_gate_context_from_db("signal_brief", db_session, run_id)
    clusters = ctx["clusters"]

    assert len(clusters) == 2
    suggested = next(c for c in clusters if c["suggested"])
    # The alphabetically-first cluster_key should be suggested
    all_keys = [c["cluster_key"] for c in clusters]
    all_keys_sorted = sorted(all_keys)
    assert suggested["cluster_key"] == all_keys_sorted[0], (
        f"Expected alpha-first key {all_keys_sorted[0]!r} to be suggested, "
        f"got {suggested['cluster_key']!r}"
    )


async def test_cluster_score_recency_bonus() -> None:
    """Unit test: recency bonus fires when a signal was captured recently."""
    from unittest.mock import MagicMock

    now = datetime.now(UTC)
    recent = now - timedelta(days=3)
    old = now - timedelta(days=30)

    sig_recent = MagicMock()
    sig_recent.qualification_json = {"fit_score": 0.5}
    sig_recent.created_at = recent

    sig_old = MagicMock()
    sig_old.qualification_json = {"fit_score": 0.5}
    sig_old.created_at = old

    score_with_recent, reason_with_recent = _cluster_score([sig_recent], now_utc=now)
    score_without_recent, reason_without_recent = _cluster_score([sig_old], now_utc=now)

    assert score_with_recent > score_without_recent
    assert "recent activity" in reason_with_recent
    assert "recent activity" not in reason_without_recent


async def test_cluster_score_stacking_bonus() -> None:
    """Unit test: stacking bonus increases with more signals, capped at +0.20."""
    from unittest.mock import MagicMock

    now = datetime.now(UTC)
    old = now - timedelta(days=30)

    def _make_mock(fit: float) -> Any:
        sig = MagicMock()
        sig.qualification_json = {"fit_score": fit}
        sig.created_at = old
        return sig

    score_1, _ = _cluster_score([_make_mock(0.5)], now_utc=now)
    score_2, _ = _cluster_score([_make_mock(0.5), _make_mock(0.5)], now_utc=now)
    score_5, _ = _cluster_score([_make_mock(0.5)] * 5, now_utc=now)
    # 5 signals → +0.20 max bonus, not +0.25
    score_many, _ = _cluster_score([_make_mock(0.5)] * 10, now_utc=now)

    assert score_2 > score_1
    assert score_5 > score_2
    # Cap check: 10 signals should not exceed 5 signals (both hit the +0.20 cap)
    assert abs(score_many - score_5) < 1e-9, "Stacking bonus should be capped at +0.20"


async def test_cluster_score_high_fit_in_reason() -> None:
    """Unit test: high-fit label appears in reason when mean fit_score >= 0.75."""
    from unittest.mock import MagicMock

    now = datetime.now(UTC)
    old = now - timedelta(days=30)

    sig_high = MagicMock()
    sig_high.qualification_json = {"fit_score": 0.80}
    sig_high.created_at = old

    sig_low = MagicMock()
    sig_low.qualification_json = {"fit_score": 0.50}
    sig_low.created_at = old

    _, reason_high = _cluster_score([sig_high], now_utc=now)
    _, reason_low = _cluster_score([sig_low], now_utc=now)

    assert "high fit" in reason_high
    assert "high fit" not in reason_low
