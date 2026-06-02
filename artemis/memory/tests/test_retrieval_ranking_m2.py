"""Memory M2 — Retrieval ranking tests.

Asserts:
  - Observations ranked by confidence (high > low, all else equal)
  - Observations ranked by valid_until (current > expired, same content)
  - Observations ranked by evidence_count (log-boost; many > one)
  - _recency_score uses valid_from anchor (M2 behaviour)
  - _recency_score decays from valid_until for expired observations
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.retrieval import (
    RetrievalWeights,
    _compute_final_score,
    _recency_score,
    search_observations,
)
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation
from artemis.memory.tests.test_b2_embeddings import MockProvider


class _ScoreParams(TypedDict):
    fts_rank: float
    semantic_sim: float
    recency: float
    obs_score: float


_SCOPE = Scope(scope_kind="workspace", scope_id="ws-m2-ranking-test")
_NOW = datetime.now(UTC)
_PAST_30 = _NOW - timedelta(days=30)
_PAST_90 = _NOW - timedelta(days=90)


# ── Pure function tests ───────────────────────────────────────────────────────


def test_recency_score_uses_valid_from_anchor() -> None:
    """valid_from=now should give score ≈ 1.0 even if created_at is old."""
    created_old = _NOW - timedelta(days=60)
    score_with_valid_from = _recency_score(
        created_old, _NOW, 30.0, valid_from=_NOW, valid_until=None
    )
    score_without = _recency_score(created_old, _NOW, 30.0)
    assert score_with_valid_from > score_without
    assert score_with_valid_from == pytest.approx(1.0, abs=0.01)


def test_recency_score_decays_from_valid_until_when_expired() -> None:
    """Expired observation (valid_until in past) decays from valid_until, not created_at."""
    created_at = _NOW - timedelta(days=10)
    valid_until_past = _NOW - timedelta(days=30)  # expired 30 days ago
    score_expired = _recency_score(
        created_at, _NOW, 30.0, valid_from=None, valid_until=valid_until_past
    )
    # At 30 days → half-life → ~0.5
    assert score_expired == pytest.approx(0.5, abs=0.02)


def test_confidence_multiplier_orders_scores() -> None:
    """High confidence observation should rank above low confidence, all else equal."""
    base_params: _ScoreParams = {
        "fts_rank": 0.5,
        "semantic_sim": 0.5,
        "recency": 0.5,
        "obs_score": 0.5,
    }
    weights = RetrievalWeights(fts=0.30, semantic=0.40, recency=0.15, score=0.15)

    score_high = _compute_final_score(
        **base_params, weights=weights, confidence=0.9, evidence_count=1
    )
    score_low = _compute_final_score(
        **base_params, weights=weights, confidence=0.4, evidence_count=1
    )
    assert score_high > score_low


def test_evidence_count_log_boost() -> None:
    """evidence_count=10 should rank above evidence_count=1, all else equal."""
    base_params: _ScoreParams = {
        "fts_rank": 0.5,
        "semantic_sim": 0.5,
        "recency": 0.5,
        "obs_score": 0.5,
    }
    weights = RetrievalWeights(fts=0.30, semantic=0.40, recency=0.15, score=0.15)

    score_many = _compute_final_score(
        **base_params, weights=weights, confidence=0.7, evidence_count=10
    )
    score_one = _compute_final_score(
        **base_params, weights=weights, confidence=0.7, evidence_count=1
    )
    assert score_many > score_one
    # log10(10) = 1.0, so boost = 1 + 1.0 = 2.0 vs 1 + log10(1) = 1.0
    assert score_many == pytest.approx(score_one * 2.0, rel=0.01)


def test_final_score_formula_zero_confidence() -> None:
    """confidence=0 → score=0 (multiplied out)."""
    score = _compute_final_score(
        fts_rank=1.0,
        semantic_sim=1.0,
        recency=1.0,
        obs_score=1.0,
        weights=RetrievalWeights(fts=0.30, semantic=0.40, recency=0.15, score=0.15),
        confidence=0.0,
        evidence_count=1,
    )
    assert score == pytest.approx(0.0)


# ── DB integration tests ──────────────────────────────────────────────────────


async def test_ranking_current_above_expired(db_session: AsyncSession) -> None:
    """A currently-valid observation ranks above an expired one with same content."""
    provider = MockProvider()
    async with db_session.begin():
        obs_current = await write_observation(
            db_session,
            _SCOPE,
            "Amira Learning annual literacy campaign is ongoing",
            valid_from=_PAST_30,
            valid_until=None,
            embedding_provider=provider,
        )
        obs_expired = await write_observation(
            db_session,
            _SCOPE,
            "Amira Learning annual literacy campaign is scheduled",
            valid_from=_PAST_90,
            valid_until=_PAST_30,
            embedding_provider=provider,
        )

    results = await search_observations(
        db_session,
        [_SCOPE],
        "literacy campaign",
        modes=["recency"],
        provider=provider,
    )
    ids = [r.id for r in results]
    # Both should appear (both in scope, valid_until=_PAST_30 should be excluded by validity filter)
    # The expired obs should be filtered OUT by the validity window (valid_until < now)
    assert obs_current.id in ids
    assert obs_expired.id not in ids


async def test_ranking_three_observations_confidence_order(db_session: AsyncSession) -> None:
    """Three observations with same text but different confidence — high ranks first.

    Since write_observation doesn't set confidence, we use _compute_final_score
    directly to verify ordering (DB layer doesn't yet wire confidence from write_observation).
    """
    base: _ScoreParams = {"fts_rank": 0.5, "semantic_sim": 0.5, "recency": 0.5, "obs_score": 0.5}
    weights = RetrievalWeights(fts=0.25, semantic=0.35, recency=0.25, score=0.15)

    s_high = _compute_final_score(**base, weights=weights, confidence=0.95, evidence_count=1)
    s_mid = _compute_final_score(**base, weights=weights, confidence=0.6, evidence_count=1)
    s_low = _compute_final_score(**base, weights=weights, confidence=0.3, evidence_count=1)

    assert s_high > s_mid > s_low


async def test_evidence_count_boost_db(db_session: AsyncSession) -> None:
    """Verify evidence_count=3 produces a higher score than evidence_count=1 via _compute_final_score."""
    base: _ScoreParams = {"fts_rank": 0.4, "semantic_sim": 0.4, "recency": 0.6, "obs_score": 0.5}
    weights = RetrievalWeights(fts=0.25, semantic=0.35, recency=0.25, score=0.15)

    s_corroborated = _compute_final_score(**base, weights=weights, confidence=0.7, evidence_count=3)
    s_single = _compute_final_score(**base, weights=weights, confidence=0.7, evidence_count=1)

    # log10(3) ≈ 0.477, so boost ≈ 1.477 vs 1.0 — ~47.7% more
    assert s_corroborated > s_single
    expected_ratio = (1 + math.log10(3)) / (1 + math.log10(1))
    assert s_corroborated / s_single == pytest.approx(expected_ratio, rel=0.01)
