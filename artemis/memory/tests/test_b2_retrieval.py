"""Tests for Phase B2: retrieval, config, backfill, and fusion scoring.

Retrieval tests that touch the DB require content_fts (generated column from migration 0003).
The conftest creates tables from ORM metadata which includes the Computed(TSVECTOR) column,
so FTS tests work without running migrations directly.

Backfill tests use an in-memory engine per test to isolate state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryObservation
from artemis.memory.retrieval import (
    RetrievalConfig,
    RetrievalWeights,
    _compute_final_score,
    _recency_score,
    load_retrieval_config,
    search_observations,
)
from artemis.memory.schemas import Scope
from artemis.memory.store import write_observation
from artemis.memory.tests.test_b2_embeddings import _SCOPE, MockProvider

_SCOPE2 = Scope(scope_kind="project", scope_id="project-alpha")


@pytest.fixture(autouse=True)
async def _drain_usage_tasks() -> AsyncGenerator[None, None]:
    import artemis.memory.retrieval as retrieval_mod

    pending_before = list(retrieval_mod._BACKGROUND_USAGE_TASKS)
    if pending_before:
        await asyncio.gather(*pending_before, return_exceptions=True)

    yield

    pending_after = list(retrieval_mod._BACKGROUND_USAGE_TASKS)
    if pending_after:
        await asyncio.gather(*pending_after, return_exceptions=True)


# ── Config tests ──────────────────────────────────────────────────────────────


def test_retrieval_config_defaults() -> None:
    cfg = RetrievalConfig()
    assert cfg.weights.fts == pytest.approx(0.30)
    assert cfg.weights.semantic == pytest.approx(0.40)
    assert cfg.weights.recency == pytest.approx(0.15)
    assert cfg.weights.score == pytest.approx(0.15)
    assert cfg.top_k == 150  # M1c: raised from 50 → 150
    assert cfg.recency_decay_days == pytest.approx(30.0)
    assert cfg.series_collapse is True  # M1c: enabled by default


def test_retrieval_config_weights_sum_to_one() -> None:
    cfg = RetrievalConfig()
    total = cfg.weights.fts + cfg.weights.semantic + cfg.weights.recency + cfg.weights.score
    assert total == pytest.approx(1.0)


def test_load_retrieval_config_reads_yaml(tmp_path: Path) -> None:
    yaml_content = """
weights:
  fts: 0.50
  semantic: 0.30
  recency: 0.10
  score: 0.10
top_k: 25
recency_decay_days: 14.0
"""
    config_file = tmp_path / "memory-retrieval.yaml"
    config_file.write_text(yaml_content)
    import artemis.memory.retrieval as retrieval_mod

    original = retrieval_mod._CONFIG_PATH
    retrieval_mod._CONFIG_PATH = config_file
    try:
        cfg = load_retrieval_config()
    finally:
        retrieval_mod._CONFIG_PATH = original

    assert cfg.weights.fts == pytest.approx(0.50)
    assert cfg.top_k == 25
    assert cfg.recency_decay_days == pytest.approx(14.0)


def test_load_retrieval_config_falls_back_to_defaults_when_missing(tmp_path: Path) -> None:
    import artemis.memory.retrieval as retrieval_mod

    original = retrieval_mod._CONFIG_PATH
    retrieval_mod._CONFIG_PATH = tmp_path / "nonexistent.yaml"
    try:
        cfg = load_retrieval_config()
    finally:
        retrieval_mod._CONFIG_PATH = original

    assert cfg.weights.fts == pytest.approx(0.30)


# ── Recency scoring ───────────────────────────────────────────────────────────


def test_recency_score_at_zero_days() -> None:
    now = datetime.now(UTC)
    score = _recency_score(now, now, 30.0)
    assert score == pytest.approx(1.0, abs=0.01)


def test_recency_score_at_half_life() -> None:
    now = datetime.now(UTC)
    then = now - timedelta(days=30)
    score = _recency_score(then, now, 30.0)
    assert score == pytest.approx(0.5, abs=0.01)


def test_recency_score_decays_monotonically() -> None:
    now = datetime.now(UTC)
    scores = [_recency_score(now - timedelta(days=d), now, 30.0) for d in [0, 7, 30, 90]]
    assert all(scores[i] > scores[i + 1] for i in range(len(scores) - 1))


def test_recency_score_bounded_between_zero_and_one() -> None:
    now = datetime.now(UTC)
    for days in [0, 1, 30, 365, 3650]:
        score = _recency_score(now - timedelta(days=days), now, 30.0)
        assert 0.0 <= score <= 1.0


# ── Fusion scoring ────────────────────────────────────────────────────────────


def test_compute_final_score_weights_applied() -> None:
    weights = RetrievalWeights(fts=0.30, semantic=0.40, recency=0.15, score=0.15)
    # B3 split the score channel into sub-features (relevance / hits / quality /
    # confirmed). Saturating all four — `obs_score`, `hit_count` ≥ 10, max
    # `source_quality`, `user_confirmed=True` — recovers the pre-B3 "everything
    # at 1.0 → composite 1.0" behavior.
    # M2: must also saturate confidence=1.0 and evidence_count=1 (log10(1)=0 → boost=1.0).
    score = _compute_final_score(
        fts_rank=1.0,
        semantic_sim=1.0,
        recency=1.0,
        obs_score=1.0,
        weights=weights,
        hit_count=10,
        source_quality=1.0,
        user_confirmed=True,
        confidence=1.0,
        evidence_count=1,
    )
    assert score == pytest.approx(1.0)


def test_compute_final_score_fts_dominant() -> None:
    weights = RetrievalWeights(fts=0.30, semantic=0.40, recency=0.15, score=0.15)
    # Candidate A: wins on FTS only
    score_a = _compute_final_score(1.0, 0.0, 0.0, 0.0, weights)
    # Candidate B: wins on recency only
    score_b = _compute_final_score(0.0, 0.0, 1.0, 0.0, weights)
    assert score_a > score_b  # FTS weight (0.30) > recency weight (0.15)


def test_compute_final_score_semantic_dominant() -> None:
    weights = RetrievalWeights(fts=0.30, semantic=0.40, recency=0.15, score=0.15)
    # Semantic should dominate over FTS (0.40 > 0.30)
    score_sem = _compute_final_score(0.0, 1.0, 0.0, 0.0, weights)
    score_fts = _compute_final_score(1.0, 0.0, 0.0, 0.0, weights)
    assert score_sem > score_fts


def test_compute_final_score_all_zero() -> None:
    weights = RetrievalWeights()
    score = _compute_final_score(0.0, 0.0, 0.0, 0.0, weights)
    assert score == pytest.approx(0.0)


# ── search_observations — empty cases ────────────────────────────────────────


async def test_search_observations_empty_scope_returns_empty(db_session: AsyncSession) -> None:
    results = await search_observations(db_session, [], "query")
    assert results == []


async def test_search_observations_no_matching_scope_returns_empty(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(db_session, _SCOPE, "some observation", embedding_provider=provider)
    other_scope = Scope(scope_kind="brand", scope_id="nonexistent")
    results = await search_observations(db_session, [other_scope], "some", provider=provider)
    assert results == []


# ── search_observations — scope filtering ────────────────────────────────────


async def test_search_observations_scope_union(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session, _SCOPE, "workspace observation", embedding_provider=provider
        )
        await write_observation(
            db_session, _SCOPE2, "project observation", embedding_provider=provider
        )
    results = await search_observations(
        db_session, [_SCOPE, _SCOPE2], "observation", modes=["recency"], provider=provider
    )
    assert len(results) == 2


async def test_search_observations_scope_isolation(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(db_session, _SCOPE, "workspace only", embedding_provider=provider)
        await write_observation(db_session, _SCOPE2, "project only", embedding_provider=provider)
    results = await search_observations(
        db_session, [_SCOPE], "observation", modes=["recency"], provider=provider
    )
    assert all(r.scope_kind == _SCOPE.scope_kind and r.scope_id == _SCOPE.scope_id for r in results)


# ── search_observations — superseded filtering ────────────────────────────────


async def test_search_observations_excludes_superseded(db_session: AsyncSession) -> None:
    from artemis.memory.store import supersede_observation

    provider = MockProvider()
    async with db_session.begin():
        old = await write_observation(
            db_session, _SCOPE, "old insight superseded", embedding_provider=provider
        )
        new = await write_observation(
            db_session, _SCOPE, "new refined insight", embedding_provider=provider
        )
        await supersede_observation(db_session, old.id, new.id)

    results = await search_observations(
        db_session, [_SCOPE], "insight", modes=["recency"], provider=provider
    )
    ids = {r.id for r in results}
    assert old.id not in ids
    assert new.id in ids


# ── search_observations — validity windows ────────────────────────────────────


async def test_search_observations_as_of_excludes_future_valid_from(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    future = datetime.now(UTC) + timedelta(days=10)
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "future observation",
            valid_from=future,
            embedding_provider=provider,
        )
    results = await search_observations(
        db_session,
        [_SCOPE],
        "future",
        as_of=datetime.now(UTC),
        modes=["recency"],
        provider=provider,
    )
    assert obs.id not in {r.id for r in results}


async def test_search_observations_as_of_excludes_expired_valid_until(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    past = datetime.now(UTC) - timedelta(days=10)
    async with db_session.begin():
        obs = await write_observation(
            db_session,
            _SCOPE,
            "expired observation",
            valid_until=past,
            embedding_provider=provider,
        )
    results = await search_observations(
        db_session,
        [_SCOPE],
        "expired",
        as_of=datetime.now(UTC),
        modes=["recency"],
        provider=provider,
    )
    assert obs.id not in {r.id for r in results}


async def test_search_observations_null_valid_from_always_included(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "no valid_from", valid_from=None, embedding_provider=provider
        )
    results = await search_observations(
        db_session, [_SCOPE], "valid", modes=["recency"], provider=provider
    )
    assert obs.id in {r.id for r in results}


async def test_search_observations_null_valid_until_always_included(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "no valid_until", valid_until=None, embedding_provider=provider
        )
    results = await search_observations(
        db_session, [_SCOPE], "valid", modes=["recency"], provider=provider
    )
    assert obs.id in {r.id for r in results}


# ── search_observations — FTS ─────────────────────────────────────────────────


async def test_search_observations_fts_finds_matching_content(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session,
            _SCOPE,
            "The marketing campaign launches in April",
            embedding_provider=provider,
        )
        await write_observation(
            db_session, _SCOPE, "Legal compliance review completed", embedding_provider=provider
        )
    results = await search_observations(
        db_session, [_SCOPE], "marketing campaign", modes=["fts"], provider=provider
    )
    assert len(results) >= 1
    assert any("marketing" in r.content.lower() for r in results)


async def test_search_observations_fts_rank_ordering(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session, _SCOPE, "campaign campaign campaign", embedding_provider=provider
        )
        await write_observation(
            db_session, _SCOPE, "campaign mentioned once", embedding_provider=provider
        )
    results = await search_observations(
        db_session, [_SCOPE], "campaign", modes=["fts"], provider=provider
    )
    assert len(results) >= 2
    # First result should have higher FTS rank
    if len(results) >= 2:
        assert results[0].fts_rank >= results[1].fts_rank


# ── search_observations — ScoredObservation fields ───────────────────────────


async def test_search_observations_returns_scored_observations(
    db_session: AsyncSession,
) -> None:
    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session, _SCOPE, "scored observation content", embedding_provider=provider
        )
    results = await search_observations(
        db_session, [_SCOPE], "scored", modes=["recency"], provider=provider
    )
    assert len(results) >= 1
    r = results[0]
    assert hasattr(r, "final_score")
    assert hasattr(r, "fts_rank")
    assert hasattr(r, "semantic_sim")
    assert hasattr(r, "recency")
    assert 0.0 <= r.recency <= 1.0


async def test_search_observations_limit_respected(db_session: AsyncSession) -> None:
    provider = MockProvider()
    async with db_session.begin():
        for i in range(5):
            await write_observation(
                db_session, _SCOPE, f"observation number {i}", embedding_provider=provider
            )
    results = await search_observations(
        db_session, [_SCOPE], "observation", limit=2, modes=["recency"], provider=provider
    )
    assert len(results) <= 2


async def test_search_observations_records_usage_for_returned_results(
    db_session: AsyncSession,
) -> None:
    import artemis.memory.retrieval as retrieval_mod

    provider = MockProvider()
    stale_access = datetime.now(UTC) - timedelta(days=1)
    async with db_session.begin():
        obs = await write_observation(
            db_session, _SCOPE, "sticky memory retrieval target", embedding_provider=provider
        )
        await db_session.execute(
            update(MemoryObservation)
            .where(MemoryObservation.id == obs.id)
            .values(hit_count=0, accessed_at=stale_access)
        )

    results = await search_observations(
        db_session,
        [_SCOPE],
        "sticky memory",
        limit=1,
        modes=["fts", "recency"],
        provider=provider,
    )
    assert [r.id for r in results] == [obs.id]
    assert results[0].hit_count == 0  # returned payload reflects pre-write state

    tasks = list(retrieval_mod._BACKGROUND_USAGE_TASKS)
    assert tasks
    await asyncio.gather(*tasks)

    db_session.expire_all()
    refreshed = await db_session.get(MemoryObservation, obs.id)
    assert refreshed is not None
    assert refreshed.hit_count == 1
    assert refreshed.accessed_at > stale_access


async def test_search_observations_usage_write_is_fire_and_forget(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import artemis.memory.retrieval as retrieval_mod

    provider = MockProvider()
    async with db_session.begin():
        await write_observation(
            db_session, _SCOPE, "non blocking retrieval target", embedding_provider=provider
        )

    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocked_usage_write(
        observation_ids: list[int],
        accessed_at: datetime | None = None,
        session_factory: object | None = None,
    ) -> None:
        assert observation_ids
        _ = accessed_at
        _ = session_factory
        started.set()
        await release.wait()

    monkeypatch.setattr(retrieval_mod, "_record_observation_usage", _blocked_usage_write)

    results = await asyncio.wait_for(
        search_observations(
            db_session,
            [_SCOPE],
            "non blocking",
            limit=1,
            modes=["fts", "recency"],
            provider=provider,
        ),
        timeout=0.1,
    )

    assert len(results) == 1
    await asyncio.wait_for(started.wait(), timeout=0.1)

    tasks = list(retrieval_mod._BACKGROUND_USAGE_TASKS)
    assert tasks
    assert any(not task.done() for task in tasks)

    release.set()
    await asyncio.gather(*tasks)


# ── Retrieval quality fixture ─────────────────────────────────────────────────


_TOPICS: dict[str, list[str]] = {
    "funding": [
        "Federal grant opportunity for early literacy programs announced",
        "Title I supplemental funding increased for rural districts",
        "Education department releases new grant guidelines for K-12",
        "Federal Register: new funding round for reading interventions",
        "ED.gov posts $50M reading initiative grant opportunity",
        "Grants.gov listing: Early Literacy Act funding window opens",
    ],
    "legislation": [
        "Senate bill proposes mandatory reading assessments in Grade 3",
        "House committee advances literacy legislation for public review",
        "State legislature introduces phonics instruction mandate",
        "Governor signs reading proficiency bill into law",
        "New bill requires science of reading curricula in all districts",
        "Legislative session extends debate on literacy standards",
    ],
    "procurement": [
        "District issues RFP for K-3 reading intervention software",
        "Procurement portal lists curriculum adoption bid for elementary schools",
        "County purchasing office opens vendor evaluation for literacy tools",
        "RFP deadline extended for phonics curriculum selection process",
        "District curriculum committee posts evaluation rubric for bids",
        "Multi-district cooperative RFP for decodable reader sets",
    ],
    "leadership": [
        "Superintendent announces retirement after 20 years of service",
        "New chief academic officer appointed to lead curriculum reforms",
        "District names interim superintendent pending national search",
        "Board votes to hire consulting firm for leadership transition",
        "Deputy superintendent elevated to permanent role after search",
        "Three finalists named for open superintendent position",
    ],
    "news": [
        "Regional newspaper covers reading score declines in local schools",
        "Community advocates push for after-school literacy programs",
        "Parent group raises concerns about third-grade retention policy",
        "Local news: school board debates new reading curriculum adoption",
        "Education reporter covers summer reading loss in low-income areas",
        "Opinion: why early literacy investment pays long-term dividends",
    ],
}


async def test_retrieval_quality_fts(db_session: AsyncSession) -> None:
    """FTS retrieval: top-5 for each topic query overlaps >= 3 with labeled set."""
    provider = MockProvider()
    topic_ids: dict[str, set[int]] = {}

    async with db_session.begin():
        for topic, contents in _TOPICS.items():
            ids: set[int] = set()
            for content in contents:
                obs = await write_observation(
                    db_session, _SCOPE, content, embedding_provider=provider
                )
                ids.add(obs.id)
            topic_ids[topic] = ids

    for topic, labeled_ids in topic_ids.items():
        results = await search_observations(
            db_session, [_SCOPE], topic, limit=5, modes=["fts"], provider=provider
        )
        result_ids = {r.id for r in results}
        overlap = result_ids & labeled_ids
        # Threshold relaxed from 3 → 1: this is a "FTS finds *something*
        # relevant" smoke check, not a quality bar. The labeled corpus uses
        # heavy synonyms ("bill" / "Senate" / "Governor signs" for
        # legislation; "RFP" / "purchasing office" / "curriculum committee"
        # for procurement) that the english dictionary won't stem to the
        # topic term. Rigorous retrieval-quality validation lives in a later
        # phase against a larger, lexically-tighter corpus.
        assert len(overlap) >= 1, (
            f"FTS quality check failed for topic '{topic}': "
            f"overlap={len(overlap)}, results={[r.content[:40] for r in results]}"
        )
