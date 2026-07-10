"""Memory-quality fixes (lead-fable/memory-quality) — regression tests.

Covers the four fixes from the Fable memory-subsystem audit:

1. apply_consolidation propagates M2 confidence + evidence_count derived from
   the source observations (derive_consolidated_confidence). Without this,
   merged observations landed at the DB defaults (0.5 / 1) and retrieval's
   confidence × evidence multipliers ranked them BELOW their own sources.
   This also unblocks the rule-based auto-resolution path (confidence_delta
   was previously always 0 because new_confidence was always 0.5).
2. The incremental consolidator's candidate sweep passes record_usage=False so
   internal sweeps no longer inflate hit_count/accessed_at.
3. consolidate_near_duplicates (M1b) is wired into run_maintenance.
4. _run_conflict_checks uses a BOUNDED candidate pool (_fetch_conflict_candidates:
   pgvector top-N + recency top-N) instead of loading every active observation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.consolidator import (
    ConsolidationProposal,
    _fetch_conflict_candidates,
    apply_consolidation,
    corroborate_confidence,
    derive_consolidated_confidence,
)
from artemis.memory.maintenance import run_maintenance
from artemis.memory.models import MemoryConflict, MemoryObservation
from artemis.memory.retrieval import RetrievalWeights, _compute_final_score
from artemis.memory.schemas import Observation, Scope
from artemis.memory.store import supersede_observation, write_observation

_SCOPE = Scope(scope_kind="workspace", scope_id="ws-quality-fixes")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _obs(
    obs_id: int = 1,
    confidence: float = 0.5,
    evidence_count: int = 1,
    content: str = "some observation content",
) -> Observation:
    """Minimal valid Observation for pure-logic tests."""
    from datetime import UTC, datetime

    now = datetime(2026, 7, 1, tzinfo=UTC)
    return Observation(
        id=obs_id,
        scope_kind="workspace",
        scope_id="ws-pure",
        category="discovery",
        content=content,
        content_hash=f"hash-{obs_id}",
        score=1.0,
        hit_count=0,
        source_quality=0.5,
        user_confirmed=False,
        valid_from=None,
        valid_until=None,
        superseded_by=None,
        owner_user_id=None,
        created_at=now,
        accessed_at=now,
        confidence=confidence,
        evidence_count=evidence_count,
    )


def _unrelated_adapter() -> MagicMock:
    """Mock LLM adapter that always returns UNRELATED (semantic detector no-ops)."""
    import json

    from artemis.agent.types import TextBlock

    payload = json.dumps({"verdict": "UNRELATED", "confidence": 0.05, "reason": "different facts"})
    mock_response = MagicMock()
    mock_response.message.content = [TextBlock(text=payload)]
    mock_adapter = MagicMock()
    mock_adapter.complete = AsyncMock(return_value=mock_response)
    return mock_adapter


def _patch_semantic_unrelated():
    """Patch the semantic conflict detector so only rule-based detection fires."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "artemis.memory.semantic_conflict_detector.resolve_adapter_async",
            new=AsyncMock(return_value=_unrelated_adapter()),
        )
    )
    stack.enter_context(
        patch(
            "artemis.memory.semantic_conflict_detector._shortlist_by_embedding",
            new=AsyncMock(return_value=[]),
        )
    )
    return stack


async def _set_m2(
    session: AsyncSession, obs_id: int, confidence: float, evidence_count: int
) -> Observation:
    """Set M2 fields on a row and return the refreshed Observation."""
    await session.execute(
        update(MemoryObservation)
        .where(MemoryObservation.id == obs_id)
        .values(confidence=confidence, evidence_count=evidence_count)
    )
    row = (
        await session.execute(select(MemoryObservation).where(MemoryObservation.id == obs_id))
    ).scalar_one()
    return Observation.model_validate(row)


# ══ Fix 1a: derive_consolidated_confidence formula (pure) ═════════════════════


def test_derive_no_sources_returns_db_defaults() -> None:
    assert derive_consolidated_confidence([]) == (0.5, 1)


def test_derive_single_source_is_identity() -> None:
    conf, ev = derive_consolidated_confidence([_obs(1, confidence=0.7, evidence_count=4)])
    assert conf == pytest.approx(0.7)
    assert ev == 4


def test_derive_two_sources_max_plus_one_corroboration() -> None:
    sources = [
        _obs(1, confidence=0.9, evidence_count=2),
        _obs(2, confidence=0.6, evidence_count=1),
    ]
    conf, ev = derive_consolidated_confidence(sources)
    # max(0.9, 0.6) then one corroboration step: 0.9 + (1 - 0.9) * 0.3 = 0.93
    assert conf == pytest.approx(0.93)
    assert ev == 3  # summed evidence


def test_derive_matches_iterated_corroboration_formula() -> None:
    sources = [_obs(i, confidence=0.5, evidence_count=1) for i in range(1, 5)]
    conf, ev = derive_consolidated_confidence(sources)
    expected = 0.5
    for _ in range(3):
        expected, _count = corroborate_confidence(expected, 0)
    assert conf == pytest.approx(expected)
    assert ev == 4


def test_derive_never_below_best_source_and_capped() -> None:
    sources = [_obs(i, confidence=0.98, evidence_count=5) for i in range(1, 12)]
    conf, ev = derive_consolidated_confidence(sources)
    assert conf >= 0.98
    assert conf <= 0.99  # asymptotic cap — never reaches 1.0
    assert ev == 55


def test_derive_evidence_count_floored_at_one() -> None:
    # Degenerate evidence_count=0 rows must not produce evidence_count=0
    src = _obs(1, confidence=0.6, evidence_count=0)
    _conf, ev = derive_consolidated_confidence([src])
    assert ev == 1


# ══ Fix 1b: retrieval no longer penalizes consolidated observations (pure) ════


def test_consolidated_obs_outranks_its_sources_in_final_score() -> None:
    """The headline bug: a merged obs at DB defaults (0.5 / 1) scored BELOW its
    own 0.9-confidence sources. With propagation (0.93 / 3) it must outrank them.
    """
    weights = RetrievalWeights()

    def score(confidence: float, evidence_count: int) -> float:
        return _compute_final_score(
            0.5,
            0.5,
            0.5,
            1.0,
            weights,
            confidence=confidence,
            evidence_count=evidence_count,
        )

    source_score = score(0.9, 2)
    old_merged_score = score(0.5, 1)  # pre-fix: DB defaults
    new_merged_score = score(0.93, 3)  # post-fix: derived values

    assert old_merged_score < source_score, "documents the pre-fix penalty"
    assert new_merged_score > source_score, "post-fix: curation outranks its sources"


# ══ Fix 1c: apply_consolidation propagates confidence/evidence (DB) ═══════════


async def test_apply_consolidation_propagates_confidence_and_evidence(
    db_session: AsyncSession,
) -> None:
    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _SCOPE,
            "Angela owns the state-policy relationship tracking for the northeast",
            category="discovery",
            source_quality=0.7,
        )
        obs_b = await write_observation(
            db_session,
            _SCOPE,
            "Northeast policy contacts are maintained weekly in the shared tracker",
            category="discovery",
            source_quality=0.6,
        )
        obs_a = await _set_m2(db_session, obs_a.id, confidence=0.9, evidence_count=2)
        obs_b = await _set_m2(db_session, obs_b.id, confidence=0.6, evidence_count=1)

    proposal = ConsolidationProposal(
        category="discovery",
        content="Angela maintains northeast state-policy contacts weekly in the shared tracker",
        evidence_from_ids=[obs_a.id, obs_b.id],
        source_quality=0.9,
    )

    with _patch_semantic_unrelated():
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_a.id: obs_a, obs_b.id: obs_b},
            )

    assert len(created) == 1
    merged = created[0]
    # Returned pydantic object carries the propagated values
    assert merged.confidence == pytest.approx(0.93)
    assert merged.evidence_count == 3

    # And the DB row agrees
    row = (
        await db_session.execute(select(MemoryObservation).where(MemoryObservation.id == merged.id))
    ).scalar_one()
    assert row.confidence == pytest.approx(0.93)
    assert row.evidence_count == 3
    assert row.confidence_origin == "consolidation"

    # Sources are superseded by the merged observation (lossless contract intact)
    for src_id in (obs_a.id, obs_b.id):
        src_row = (
            await db_session.execute(
                select(MemoryObservation).where(MemoryObservation.id == src_id)
            )
        ).scalar_one()
        assert src_row.superseded_by == merged.id


async def test_apply_consolidation_dedup_return_never_self_supersedes(
    db_session: AsyncSession,
) -> None:
    """When the merged content is verbatim one of the sources, write_observation's
    content-hash dedup returns that SOURCE row as 'new'. It must not supersede or
    evidence-link itself, and its confidence must never be lowered.
    """
    content_a = "Kai routes enablement questions to the product taxonomy glossary"
    async with db_session.begin():
        obs_a = await write_observation(
            db_session, _SCOPE, content_a, category="discovery", source_quality=0.7
        )
        obs_b = await write_observation(
            db_session,
            _SCOPE,
            "Enablement questions about products resolve through Kai's glossary",
            category="discovery",
            source_quality=0.6,
        )
        obs_a = await _set_m2(db_session, obs_a.id, confidence=0.95, evidence_count=4)
        obs_b = await _set_m2(db_session, obs_b.id, confidence=0.5, evidence_count=1)

    proposal = ConsolidationProposal(
        category="discovery",
        content=content_a,  # identical to obs_a → dedup returns obs_a
        evidence_from_ids=[obs_a.id, obs_b.id],
        source_quality=0.9,
    )

    with _patch_semantic_unrelated():
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_a.id: obs_a, obs_b.id: obs_b},
            )

    assert created[0].id == obs_a.id, "dedup should return the existing source row"

    row_a = (
        await db_session.execute(select(MemoryObservation).where(MemoryObservation.id == obs_a.id))
    ).scalar_one()
    assert row_a.superseded_by is None, "must never self-supersede"
    # derived = corroborate(max(0.95, 0.5)) once = 0.965; existing 0.95 → max wins
    assert row_a.confidence == pytest.approx(0.965)
    assert row_a.evidence_count == 5  # sum(4 + 1) > existing 4

    row_b = (
        await db_session.execute(select(MemoryObservation).where(MemoryObservation.id == obs_b.id))
    ).scalar_one()
    assert row_b.superseded_by == obs_a.id


async def test_rule_based_auto_resolution_fires_with_propagated_confidence(
    db_session: AsyncSession,
) -> None:
    """Auto-resolution requires confidence_delta > 0.3 AND evidence_ratio > 2.
    Pre-fix this could NEVER fire from apply_consolidation because the merged
    obs always sat at confidence 0.5 / evidence 1. With propagation (0.93 / 3
    here) a stale default-confidence conflicting obs is auto-superseded.
    """
    async with db_session.begin():
        # The stale observation that will conflict (same 4-word prefix rule)
        stale = await write_observation(
            db_session,
            _SCOPE,
            "Jon prefers morning meetings every single week",
            category="discovery",
            source_quality=0.5,
        )
        stale_id = stale.id

        src_1 = await write_observation(
            db_session,
            _SCOPE,
            "Calendar shows recurring Friday-only meeting slots for Jon",
            category="discovery",
            source_quality=0.9,
        )
        src_2 = await write_observation(
            db_session,
            _SCOPE,
            "Assistant notes say meetings moved to Fridays permanently",
            category="discovery",
            source_quality=0.8,
        )
        src_1 = await _set_m2(db_session, src_1.id, confidence=0.9, evidence_count=2)
        src_2 = await _set_m2(db_session, src_2.id, confidence=0.8, evidence_count=1)

    proposal = ConsolidationProposal(
        category="discovery",
        # Same first-4-words prefix as `stale`, different remainder →
        # rule-based incompatible_values conflict.
        content="Jon prefers morning meetings on Fridays only",
        evidence_from_ids=[src_1.id, src_2.id],
        source_quality=0.9,
    )

    with _patch_semantic_unrelated():
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {src_1.id: src_1, src_2.id: src_2},
            )

    merged = created[0]
    assert merged.confidence == pytest.approx(0.93)
    assert merged.evidence_count == 3

    # The stale observation was auto-superseded (delta 0.43 > 0.3, ratio 3 > 2)
    stale_row = (
        await db_session.execute(select(MemoryObservation).where(MemoryObservation.id == stale_id))
    ).scalar_one()
    assert stale_row.superseded_by == merged.id

    # And an auto-resolved conflict row was written
    conflicts = list((await db_session.execute(select(MemoryConflict))).scalars())
    pair = (min(stale_id, merged.id), max(stale_id, merged.id))
    matching = [c for c in conflicts if (c.observation_a_id, c.observation_b_id) == pair]
    assert matching, f"expected a conflict row for {pair}"
    assert matching[0].resolution == "auto"
    assert matching[0].conflict_type == "incompatible_values"


# ══ Fix 2: incremental sweep must not pollute hit_count ═══════════════════════


async def test_incremental_sweep_passes_record_usage_false(monkeypatch) -> None:
    from artemis.memory import incremental_consolidator as ic

    captured: dict[str, object] = {}

    async def fake_search(session, scope_set, query, *args, **kwargs):
        captured["record_usage"] = kwargs.get("record_usage", "NOT PASSED")
        return []

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    monkeypatch.setattr("artemis.db.SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr("artemis.memory.retrieval.search_observations", fake_search)

    consolidator = ic.IncrementalConsolidator()
    key = ic._SlotKey("workspace", "ws-sweep", "discovery")
    await consolidator._run_consolidation(key)

    assert captured["record_usage"] is False, (
        "internal consolidation sweep must not inflate hit_count/accessed_at"
    )


# ══ Fix 3: near-duplicate consolidation wired into maintenance ════════════════


async def test_run_maintenance_collapses_clone_clusters(
    db_session: AsyncSession,
) -> None:
    """End-to-end effect: two timestamp-prefixed clones survive the content-hash
    dedup, and one maintenance pass collapses them losslessly.
    """
    async with db_session.begin():
        keep = await write_observation(
            db_session,
            _SCOPE,
            "[2026-06-06T16:11:24+00:00] [USER] Still an echo. Not responding.",
            category="discovery",
            source_quality=0.5,
        )
        dupe = await write_observation(
            db_session,
            _SCOPE,
            "[2026-06-07T09:02:10+00:00] [USER] Still an echo.  Not responding.",
            category="discovery",
            source_quality=0.5,
        )
    assert keep.id != dupe.id, "timestamp prefixes must defeat the content-hash dedup"

    async with db_session.begin():
        result = await run_maintenance(db_session)

    assert result["near_duplicate_clusters"] == 1
    assert result["near_duplicate_superseded"] == 1

    rows = {
        r.id: r
        for r in (
            await db_session.execute(
                select(MemoryObservation).where(MemoryObservation.id.in_([keep.id, dupe.id]))
            )
        ).scalars()
    }
    active = [r for r in rows.values() if r.superseded_by is None]
    superseded = [r for r in rows.values() if r.superseded_by is not None]
    assert len(active) == 1 and len(superseded) == 1
    assert superseded[0].superseded_by == active[0].id


async def test_run_maintenance_survives_near_duplicate_failure(
    db_session: AsyncSession, monkeypatch
) -> None:
    """FAIL SAFE: a near-duplicate pass failure must not abort the maintenance
    transaction or lose the score-decay results (savepoint isolation).
    """
    async with db_session.begin():
        await write_observation(
            db_session,
            _SCOPE,
            "A discovery observation that should still decay",
            category="discovery",
            source_quality=0.5,
        )

    async def _boom(session, **kwargs):
        raise RuntimeError("simulated near-duplicate failure")

    monkeypatch.setattr("artemis.memory.near_duplicate.consolidate_near_duplicates", _boom)

    async with db_session.begin():
        result = await run_maintenance(db_session)

    assert result["discovery"] == 1, "score decay must still apply"
    assert "near_duplicate_clusters" not in result


# ══ Fix 4: bounded conflict-check candidate pool ══════════════════════════════


async def test_fetch_conflict_candidates_small_scope_equivalence(
    db_session: AsyncSession,
) -> None:
    """For scopes with <= limit active observations the bounded pool must equal
    the old unbounded scan: every active peer, minus new_obs and superseded rows.
    """
    contents = [
        "Alpha fact about the quarterly campaign metrics dashboard",
        "Beta note describing the district outreach cadence",
        "Gamma record of the Slack channel migration decision",
        "Delta summary of the enablement content sync plan",
        "Epsilon detail on the calendar API scope grant",
    ]
    ids: list[int] = []
    async with db_session.begin():
        for content in contents:
            obs = await write_observation(
                db_session, _SCOPE, content, category="discovery", source_quality=0.5
            )
            ids.append(obs.id)
        new_obs = await write_observation(
            db_session,
            _SCOPE,
            "Zeta observation that conflict checks run against the pool",
            category="discovery",
            source_quality=0.5,
        )
        # Retire one peer — must not appear in the pool
        await supersede_observation(db_session, ids[0], new_obs.id)

    candidates = await _fetch_conflict_candidates(db_session, _SCOPE, new_obs.id)
    candidate_ids = {c.id for c in candidates}

    assert candidate_ids == set(ids[1:]), (
        f"expected exactly the active peers {set(ids[1:])}, got {candidate_ids}"
    )
    assert new_obs.id not in candidate_ids


async def test_fetch_conflict_candidates_respects_limit(
    db_session: AsyncSession,
) -> None:
    """Large scopes are bounded: at most 2 × limit rows (vector top-N ∪ recency
    top-N), never the full table scan."""
    async with db_session.begin():
        for i in range(12):
            await write_observation(
                db_session,
                _SCOPE,
                f"Filler observation number {i} with distinct wording variant {i * 7}",
                category="discovery",
                source_quality=0.5,
            )
        new_obs = await write_observation(
            db_session,
            _SCOPE,
            "Probe observation used as the conflict-check anchor",
            category="discovery",
            source_quality=0.5,
        )

    candidates = await _fetch_conflict_candidates(db_session, _SCOPE, new_obs.id, limit=3)
    assert 1 <= len(candidates) <= 6, f"pool must be bounded by 2 x limit; got {len(candidates)}"
    assert all(c.id != new_obs.id for c in candidates)
    assert all(c.superseded_by is None for c in candidates)
