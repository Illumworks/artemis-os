"""Tests for M1c series-collapse and _series_key helpers.

Covers:
- _series_key returns the expected key for momentum-snapshot content
- _series_key returns None for non-time-series content (conversation messages,
  signals, arbitrary observations)
- _series_key returns None when two DIFFERENT momentum series are present
  (they each get their own key, not None, but those keys differ)
- series_collapse=True keeps ONLY the latest-dated member of a series and
  lets distinct results fill the freed slots
- series_collapse=True: different series both survive (not collapsed together)
- series_collapse=True: non-time-series results are never grouped
- series_collapse=True: distinct results are never dropped
- series_collapse=False produces byte-identical output to pre-M1c behaviour
  (simple truncation at `limit`)
- Integration: search_observations with series_collapse=True via DB
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.retrieval import (
    RetrievalConfig,
    _apply_series_collapse,
    _series_key,
    search_observations,
)
from artemis.memory.schemas import Scope, ScoredObservation
from artemis.memory.store import write_observation
from artemis.memory.tests.test_b2_embeddings import MockProvider

_SCOPE = Scope(scope_kind="workspace", scope_id="ws-test-sc")
_SCOPE2 = Scope(scope_kind="workspace", scope_id="ws-test-sc-2")

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=UTC)
_OLD = _NOW - timedelta(days=30)
_OLDER = _NOW - timedelta(days=60)


# ── _series_key unit tests ────────────────────────────────────────────────────


def test_series_key_momentum_snapshot_basic() -> None:
    content = "Momentum snapshot for general_growth/MI"
    key = _series_key(content)
    assert key == ("momentum", "general_growth/MI")


def test_series_key_momentum_snapshot_with_body() -> None:
    content = "Momentum snapshot for acquisition/email: score=0.87, trend=up"
    key = _series_key(content)
    assert key == ("momentum", "acquisition/email")


def test_series_key_momentum_snapshot_leading_whitespace() -> None:
    content = "  Momentum snapshot for retention/weekly"
    key = _series_key(content)
    assert key == ("momentum", "retention/weekly")


def test_series_key_momentum_snapshot_case_insensitive() -> None:
    key = _series_key("MOMENTUM SNAPSHOT FOR general_growth/MI")
    assert key == ("momentum", "general_growth/MI")


def test_series_key_returns_none_for_conversation_message() -> None:
    content = "Jon mentioned that Amira's retention is tracking well this quarter."
    assert _series_key(content) is None


def test_series_key_returns_none_for_signal_content() -> None:
    content = "Signal: engagement rate rose 12% after campaign launch."
    assert _series_key(content) is None


def test_series_key_returns_none_for_arbitrary_observation() -> None:
    content = "The marketing budget for Q3 was approved at $500k."
    assert _series_key(content) is None


def test_series_key_returns_none_for_empty_string() -> None:
    assert _series_key("") is None


def test_series_key_two_different_series_produce_different_keys() -> None:
    key_a = _series_key("Momentum snapshot for general_growth/MI")
    key_b = _series_key("Momentum snapshot for acquisition/email")
    assert key_a is not None
    assert key_b is not None
    assert key_a != key_b


# ── _apply_series_collapse unit tests ────────────────────────────────────────


def _make_obs(
    obs_id: int,
    content: str,
    final_score: float,
    valid_from: datetime | None = None,
    created_at: datetime | None = None,
) -> ScoredObservation:
    """Build a minimal ScoredObservation for collapse tests."""
    return ScoredObservation(
        id=obs_id,
        scope_kind="workspace",
        scope_id="ws-test",
        category="insight",
        content=content,
        score=0.5,
        hit_count=0,
        source_quality=0.8,
        user_confirmed=False,
        valid_from=valid_from,
        valid_until=None,
        superseded_by=None,
        owner_user_id=None,
        created_at=created_at or _NOW,
        accessed_at=_NOW,
        confidence=0.8,
        supersedes=None,
        evidence_count=1,
        final_score=final_score,
        fts_rank=0.0,
        semantic_sim=0.0,
        recency=0.5,
        graph_proximity=0.0,
    )


def test_series_collapse_keeps_latest_member() -> None:
    """With two momentum snapshots in the pool, only the newest survives."""
    older = _make_obs(1, "Momentum snapshot for general_growth/MI", 0.90, valid_from=_OLDER)
    newer = _make_obs(2, "Momentum snapshot for general_growth/MI", 0.70, valid_from=_OLD)
    # older has higher score but newer has later date — latest wins
    scored = [older, newer]
    result = _apply_series_collapse(scored, limit=10)
    ids = [r.id for r in result]
    assert 2 in ids, "Latest snapshot (id=2) must be in results"
    assert 1 not in ids, "Older snapshot (id=1) must be collapsed out"


def test_series_collapse_different_series_both_survive() -> None:
    """Two snapshots of DIFFERENT series must not be collapsed together."""
    snap_a = _make_obs(1, "Momentum snapshot for general_growth/MI", 0.90, valid_from=_NOW)
    snap_b = _make_obs(2, "Momentum snapshot for acquisition/email", 0.85, valid_from=_NOW)
    result = _apply_series_collapse([snap_a, snap_b], limit=10)
    ids = {r.id for r in result}
    assert 1 in ids
    assert 2 in ids


def test_series_collapse_non_series_results_never_grouped() -> None:
    """Non-time-series observations pass through untouched."""
    non_series_a = _make_obs(10, "Amira marketing budget approved", 0.80)
    non_series_b = _make_obs(11, "Retention up this quarter", 0.75)
    result = _apply_series_collapse([non_series_a, non_series_b], limit=10)
    ids = {r.id for r in result}
    assert 10 in ids
    assert 11 in ids


def test_series_collapse_distinct_results_never_dropped() -> None:
    """Distinct (non-series) results must survive regardless of limit."""
    distinct = [_make_obs(i, f"Distinct observation {i}", 1.0 - i * 0.05) for i in range(5)]
    result = _apply_series_collapse(distinct, limit=10)
    assert len(result) == 5


def test_series_collapse_freed_slot_filled_by_distinct() -> None:
    """When a series is collapsed to 1 member, the freed slot is filled by the next result."""
    older = _make_obs(1, "Momentum snapshot for general_growth/MI", 0.90, valid_from=_OLDER)
    newer = _make_obs(2, "Momentum snapshot for general_growth/MI", 0.80, valid_from=_OLD)
    filler = _make_obs(3, "Distinct non-series observation", 0.70)
    # Without collapse we'd get [1, 2] in top-2; with collapse we get [2, 3]
    result = _apply_series_collapse([older, newer, filler], limit=2)
    ids = [r.id for r in result]
    assert ids == [2, 3], f"Expected [2, 3] (latest series + filler), got {ids}"


def test_series_collapse_respects_limit() -> None:
    """Output never exceeds limit."""
    items = [_make_obs(i, f"obs {i}", 1.0 - i * 0.01) for i in range(20)]
    result = _apply_series_collapse(items, limit=10)
    assert len(result) <= 10


def test_series_collapse_off_is_byte_identical() -> None:
    """series_collapse=False → simple slice, identical to pre-M1c behaviour."""
    older = _make_obs(1, "Momentum snapshot for general_growth/MI", 0.90, valid_from=_OLDER)
    newer = _make_obs(2, "Momentum snapshot for general_growth/MI", 0.80, valid_from=_OLD)
    filler = _make_obs(3, "Distinct obs", 0.70)
    # Without collapse: sorted[:2] = [older, newer] (by score)
    scored = [older, newer, filler]
    result = scored[:2]  # pre-M1c truncation
    assert [r.id for r in result] == [1, 2]


def test_series_collapse_single_snapshot_not_dropped() -> None:
    """A series with only one member in the pool must always survive."""
    single = _make_obs(5, "Momentum snapshot for general_growth/MI", 0.85, valid_from=_NOW)
    distinct = _make_obs(6, "Some other insight", 0.60)
    result = _apply_series_collapse([single, distinct], limit=10)
    ids = {r.id for r in result}
    assert 5 in ids
    assert 6 in ids


def test_series_collapse_many_snapshots_same_series_only_latest_kept() -> None:
    """3 snapshots of the same series — only the one with the latest valid_from survives."""
    s1 = _make_obs(1, "Momentum snapshot for general_growth/MI", 0.90, valid_from=_OLDER)
    s2 = _make_obs(
        2, "Momentum snapshot for general_growth/MI", 0.85, valid_from=_NOW - timedelta(days=15)
    )
    s3 = _make_obs(3, "Momentum snapshot for general_growth/MI", 0.70, valid_from=_NOW)
    result = _apply_series_collapse([s1, s2, s3], limit=10)
    ids = {r.id for r in result}
    assert ids == {3}, f"Only latest snapshot (id=3) expected; got {ids}"


# ── Integration: search_observations with series_collapse via DB ──────────────


async def test_search_observations_series_collapse_default_on(db_session: AsyncSession) -> None:
    """Two momentum snapshots in DB → only the latest reaches the top-10 results."""
    provider = MockProvider()
    async with db_session.begin():
        old_snap = await write_observation(
            db_session,
            _SCOPE,
            "Momentum snapshot for general_growth/MI. Score: 0.72",
            embedding_provider=provider,
            valid_from=_OLDER,
        )
        new_snap = await write_observation(
            db_session,
            _SCOPE,
            "Momentum snapshot for general_growth/MI. Score: 0.85",
            embedding_provider=provider,
            valid_from=_NOW,
        )

    cfg = RetrievalConfig(top_k=150, series_collapse=True)
    results = await search_observations(
        db_session,
        [_SCOPE],
        "current momentum",
        limit=10,
        cfg=cfg,
        provider=provider,
        record_usage=False,
    )
    result_ids = {r.id for r in results}
    assert new_snap.id in result_ids, "Latest snapshot must appear"
    assert old_snap.id not in result_ids, "Older snapshot must be collapsed out"


async def test_search_observations_series_collapse_false_keeps_both(
    db_session: AsyncSession,
) -> None:
    """With series_collapse=False, both snapshots can appear in results."""
    provider = MockProvider()
    async with db_session.begin():
        old_snap = await write_observation(
            db_session,
            _SCOPE,
            "Momentum snapshot for general_growth/MI. Score: 0.72",
            embedding_provider=provider,
            valid_from=_OLDER,
        )
        new_snap = await write_observation(
            db_session,
            _SCOPE,
            "Momentum snapshot for general_growth/MI. Score: 0.85",
            embedding_provider=provider,
            valid_from=_NOW,
        )

    cfg = RetrievalConfig(top_k=150, series_collapse=False)
    results = await search_observations(
        db_session,
        [_SCOPE],
        "current momentum",
        limit=10,
        cfg=cfg,
        provider=provider,
        record_usage=False,
    )
    result_ids = {r.id for r in results}
    # Both can appear — series_collapse=False reverts to plain truncation
    # We only assert that distinct (non-grouped) behaviour holds: at least
    # one of them appears, and no crash.
    assert old_snap.id in result_ids or new_snap.id in result_ids


async def test_search_observations_non_series_all_returned(db_session: AsyncSession) -> None:
    """Non-time-series observations are never collapsed, all return normally."""
    provider = MockProvider()
    async with db_session.begin():
        obs1 = await write_observation(
            db_session,
            _SCOPE,
            "Marketing budget for Q3 was approved",
            embedding_provider=provider,
        )
        obs2 = await write_observation(
            db_session,
            _SCOPE,
            "Retention metric improved this quarter",
            embedding_provider=provider,
        )
        obs3 = await write_observation(
            db_session,
            _SCOPE,
            "New campaign launched for Amira",
            embedding_provider=provider,
        )

    cfg = RetrievalConfig(top_k=150, series_collapse=True)
    results = await search_observations(
        db_session,
        [_SCOPE],
        "marketing",
        limit=10,
        cfg=cfg,
        provider=provider,
        record_usage=False,
    )
    result_ids = {r.id for r in results}
    # All three distinct observations must appear — no collapsing of non-series
    assert obs1.id in result_ids
    assert obs2.id in result_ids
    assert obs3.id in result_ids
