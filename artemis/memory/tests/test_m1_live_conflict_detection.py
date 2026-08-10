"""M1 live-path smoke tests — conflict detection on the real apply_consolidation sweep.

These tests drive the ACTUAL live path:
  write_observation → apply_consolidation (as incremental_consolidator._run_consolidation
  calls it) → asserts DB effects (superseded_by / memory_conflicts rows).

The tests use a mocked LLM for semantic detection so they do not require a running
provider, while still exercising the real DB writes and the real control flow.

Why this matters: the prior M1 build wired detect_semantic_conflicts into
write_observation_with_conflict_check which has ZERO non-test callers.  These tests
prove the detectors run on the live path (apply_consolidation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.consolidator import (
    ConsolidationProposal,
    apply_consolidation,
)
from artemis.memory.models import MemoryConflict, MemoryObservation
from artemis.memory.retrieval import search_observations
from artemis.memory.schemas import Observation, Scope
from artemis.memory.store import write_observation

_SCOPE = Scope(scope_kind="workspace", scope_id="ws-m1-live-smoke")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _contradict_adapter(confidence: float = 0.91) -> MagicMock:
    """Build a mock LLM adapter that always returns CONTRADICT."""
    import json

    from artemis.agent.types import TextBlock

    payload = json.dumps(
        {
            "verdict": "CONTRADICT",
            "confidence": confidence,
            "reason": "roles are mutually exclusive",
        }
    )
    mock_response = MagicMock()
    mock_response.message.content = [TextBlock(text=payload)]
    mock_adapter = MagicMock()
    mock_adapter.complete = AsyncMock(return_value=mock_response)
    return mock_adapter


def _unrelated_adapter() -> MagicMock:
    """Build a mock LLM adapter that always returns UNRELATED (no conflict)."""
    import json

    from artemis.agent.types import TextBlock

    payload = json.dumps({"verdict": "UNRELATED", "confidence": 0.05, "reason": "different facts"})
    mock_response = MagicMock()
    mock_response.message.content = [TextBlock(text=payload)]
    mock_adapter = MagicMock()
    mock_adapter.complete = AsyncMock(return_value=mock_response)
    return mock_adapter


def _refine_adapter() -> MagicMock:
    """Build a mock LLM adapter that always returns REFINE (additive update)."""
    import json

    from artemis.agent.types import TextBlock

    payload = json.dumps({"verdict": "REFINE", "confidence": 0.70, "reason": "B adds detail to A"})
    mock_response = MagicMock()
    mock_response.message.content = [TextBlock(text=payload)]
    mock_adapter = MagicMock()
    mock_adapter.complete = AsyncMock(return_value=mock_response)
    return mock_adapter


def _patch_semantic_adapter(mock_adapter: MagicMock):
    """Context manager: patch resolve_adapter_async to return mock_adapter + bypass DB session."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch(
            "artemis.memory.semantic_conflict_detector.resolve_adapter_async",
            new=AsyncMock(return_value=mock_adapter),
        )
    )
    # SessionLocal is imported lazily inside detect_semantic_conflicts as artemis.db.SessionLocal
    mock_session_local = MagicMock()
    mock_session_local.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
    mock_session_local.return_value.__aexit__ = AsyncMock(return_value=None)
    stack.enter_context(patch("artemis.db.SessionLocal", new=mock_session_local))
    return stack


def _patch_shortlist_all(sim: float = 0.82):
    """Patch _shortlist_by_embedding to return all candidates at the given similarity."""
    return patch(
        "artemis.memory.semantic_conflict_detector._shortlist_by_embedding",
        new=AsyncMock(
            side_effect=lambda session, new_obs, candidates, **kw: [
                (cand, sim) for cand in candidates
            ]
        ),
    )


# ── Test 1: contradiction detected → memory_conflicts row + rule-based fires ──


@pytest.mark.asyncio
async def test_apply_consolidation_detects_rule_based_conflict(
    db_session: AsyncSession,
) -> None:
    """Rule-based conflict: two obs share the same 4-word prefix, different suffix.

    apply_consolidation writes obs B (the consolidated observation), then
    _run_conflict_checks should detect that obs A is still active and conflicts.

    BEFORE: obs A active, no conflict rows
    AFTER:  memory_conflicts row with resolution=NULL exists (confidence_delta too small
            for auto-resolve since consolidated obs has default source_quality)
    """
    obs_a_id: int
    obs_c_id: int
    obs_c: Observation

    # Write obs A and obs C in one transaction
    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _SCOPE,
            "Jon Fila leads the marketing department at Amira Learning",
            category="discovery",
            source_quality=0.7,
        )
        obs_a_id = obs_a.id

        # obs C is the "source" that gets consolidated into obs B
        obs_c = await write_observation(
            db_session,
            _SCOPE,
            "Amira Learning serves K-12 students nationwide",
            category="discovery",
            source_quality=0.6,
        )
        obs_c_id = obs_c.id

    # Consolidated obs B: same 4-word prefix as obs A → rule-based fires
    # obs A is NOT in evidence_from_ids so it remains active for conflict detection
    proposal = ConsolidationProposal(
        category="discovery",
        content="Jon Fila leads the sales division at Amira Learning",
        evidence_from_ids=[obs_c_id],
        supersedes_ids=[],
        source_quality=0.9,
    )

    # Semantic adapter returns UNRELATED so only rule-based fires
    adapter = _unrelated_adapter()
    with _patch_shortlist_all(0.62), _patch_semantic_adapter(adapter):
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_c_id: obs_c},
            )

    assert len(created) == 1, "Should create 1 new consolidated observation"
    new_obs_b = created[0]

    # ── ASSERT DB EFFECTS ──────────────────────────────────────────────────────

    # 1. A memory_conflicts row must exist for (obs_a, new_obs_b)
    result = await db_session.execute(select(MemoryConflict))
    conflicts = list(result.scalars())
    assert len(conflicts) >= 1, (
        f"Expected at least one conflict row; got none. "
        f"obs_a_id={obs_a_id}, new_obs_b.id={new_obs_b.id}"
    )

    pair_ids = {(c.observation_a_id, c.observation_b_id) for c in conflicts}
    expected_pair = (min(obs_a_id, new_obs_b.id), max(obs_a_id, new_obs_b.id))
    assert expected_pair in pair_ids, (
        f"Expected conflict pair {expected_pair} not found in {pair_ids}"
    )

    # 2. Conflict type should be incompatible_values (same 4-word prefix)
    matching = [c for c in conflicts if (c.observation_a_id, c.observation_b_id) == expected_pair]
    assert matching[0].conflict_type == "incompatible_values"

    # 3. obs A should still be active (confidence_delta < 0.3 for auto-supersede)
    result = await db_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_a_id)
    )
    obs_a_after = result.scalar_one()
    assert obs_a_after.superseded_by is None, (
        "obs A should NOT be auto-superseded when confidence_delta is too small"
    )


# ── Test 2: semantic-only contradiction → auto-supersede via live path ─────────


@pytest.mark.asyncio
async def test_apply_consolidation_detects_semantic_conflict_via_live_path(
    db_session: AsyncSession,
) -> None:
    """Semantic conflict that rule-based misses: paraphrased contradiction.

    "Jon is CMO" vs "Jon leads sales, not marketing" — no shared 4-word prefix,
    but the semantic judge (mocked to CONTRADICT, confidence 0.91) fires.

    With confidence 0.91 >= _AUTO_SUPERSEDE_THRESHOLD (0.85) → auto_resolve=True
    → obs A superseded by new_obs_b, memory_conflicts row with resolution='auto'.

    BEFORE: obs A active, 0 conflict rows
    AFTER:
      - obs A.superseded_by = new_obs_b.id
      - memory_conflicts row: type=semantic_contradiction, resolution='auto'
      - search_observations("Jon CMO") returns new_obs_b, NOT obs A
    """
    obs_a_id: int
    obs_d_id: int
    obs_d: Observation

    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _SCOPE,
            "Jon Fila serves as Chief Marketing Officer at Amira Learning",
            category="discovery",
            source_quality=0.7,
        )
        obs_a_id = obs_a.id

        # dummy source for the consolidation proposal
        obs_d = await write_observation(
            db_session,
            _SCOPE,
            "Amira Learning is a literacy platform for K-12",
            category="discovery",
            source_quality=0.6,
        )
        obs_d_id = obs_d.id

    # Consolidated obs B: paraphrased contradiction (different prefix → rule-based misses)
    proposal = ConsolidationProposal(
        category="discovery",
        content="Jon Fila now leads the sales team at Amira, not marketing",
        evidence_from_ids=[obs_d_id],
        supersedes_ids=[],
        source_quality=0.9,
    )

    # High-confidence CONTRADICT → auto_resolve=True → should supersede obs A
    adapter = _contradict_adapter(confidence=0.91)
    with _patch_shortlist_all(0.82), _patch_semantic_adapter(adapter):
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_d_id: obs_d},
            )

    assert len(created) == 1
    new_obs_b = created[0]

    # ── ASSERT DB EFFECTS ──────────────────────────────────────────────────────

    # 1. obs A must be superseded by new_obs_b
    result = await db_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_a_id)
    )
    obs_a_after = result.scalar_one()
    assert obs_a_after.superseded_by == new_obs_b.id, (
        f"HEADLINE: obs A (id={obs_a_id}) should be superseded by new_obs_b "
        f"(id={new_obs_b.id}), got superseded_by={obs_a_after.superseded_by}"
    )

    # 2. A semantic_contradiction conflict row must exist
    result = await db_session.execute(
        select(MemoryConflict).where(MemoryConflict.conflict_type == "semantic_contradiction")
    )
    sem_conflicts = list(result.scalars())
    assert len(sem_conflicts) >= 1, "Expected at least one semantic_contradiction row"

    pair = {sem_conflicts[0].observation_a_id, sem_conflicts[0].observation_b_id}
    assert obs_a_id in pair and new_obs_b.id in pair, (
        f"Conflict pair {pair} should contain obs_a_id={obs_a_id} and new_obs_b.id={new_obs_b.id}"
    )

    # 3. Resolution should be 'auto' (confidence 0.91 >= 0.85)
    assert sem_conflicts[0].resolution == "auto", (
        f"Expected resolution='auto', got {sem_conflicts[0].resolution!r}"
    )

    # 4. search_observations: obs A not in results, new_obs_b is
    search_results = await search_observations(
        db_session, [_SCOPE], "Jon CMO", limit=20, modes=["recency"], record_usage=False
    )
    active_ids = {o.id for o in search_results}
    assert obs_a_id not in active_ids, (
        "RETRIEVAL FLIP: superseded obs A must not appear in search_observations"
    )
    assert new_obs_b.id in active_ids, (
        "RETRIEVAL FLIP: new consolidated obs B must appear in search_observations"
    )


# ── Test 3: additive facts → no supersede, no conflict row ────────────────────


@pytest.mark.asyncio
async def test_apply_consolidation_additive_facts_no_conflict(
    db_session: AsyncSession,
) -> None:
    """Precision test: two unrelated facts about the same entity.

    apply_consolidation should NOT produce a conflict row and should NOT
    supersede obs A when the semantic judge returns UNRELATED.
    """
    obs_a_id: int
    obs_e_id: int
    obs_e: Observation

    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _SCOPE,
            "Amira Learning raised $25M in Series B funding in 2021",
            category="discovery",
            source_quality=0.8,
        )
        obs_a_id = obs_a.id

        obs_e = await write_observation(
            db_session,
            _SCOPE,
            "Amira Learning operates in K-12 literacy with AI-driven tools",
            category="discovery",
            source_quality=0.7,
        )
        obs_e_id = obs_e.id

    # Consolidated obs: different company fact (not contradicting obs A)
    proposal = ConsolidationProposal(
        category="discovery",
        content="Amira Learning serves students from grades K through 5 with phonics curriculum",
        evidence_from_ids=[obs_e_id],
        supersedes_ids=[],
        source_quality=0.9,
    )

    # Semantic judge: UNRELATED
    adapter = _unrelated_adapter()
    with _patch_shortlist_all(0.63), _patch_semantic_adapter(adapter):
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_e_id: obs_e},
            )

    assert len(created) == 1

    # ── ASSERT: zero conflicts, obs A still active ──────────────────────────

    result = await db_session.execute(select(MemoryConflict))
    conflicts = list(result.scalars())
    assert len(conflicts) == 0, (
        f"PRECISION: No conflict rows expected for additive facts; got {len(conflicts)}"
    )

    result = await db_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_a_id)
    )
    obs_a_after = result.scalar_one()
    assert obs_a_after.superseded_by is None, (
        "PRECISION: obs A should remain active for additive (non-contradictory) facts"
    )


# ── Test 4: temporal refinement (REFINE verdict) → no conflict ────────────────


@pytest.mark.asyncio
async def test_apply_consolidation_temporal_refinement_no_conflict(
    db_session: AsyncSession,
) -> None:
    """Temporal refinement (A was true, B is current state) = REFINE, not CONTRADICT.

    Precision test: REFINE verdict should not produce any conflict row or auto-supersede.
    """
    obs_a_id: int
    obs_f_id: int
    obs_f: Observation

    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _SCOPE,
            "Jon Fila was the CMO of Amira Learning through Q1 2026",
            category="discovery",
            source_quality=0.7,
        )
        obs_a_id = obs_a.id

        obs_f = await write_observation(
            db_session,
            _SCOPE,
            "Jon Fila served in various leadership roles at Amira",
            category="discovery",
            source_quality=0.6,
        )
        obs_f_id = obs_f.id

    proposal = ConsolidationProposal(
        category="discovery",
        content="As of June 2026, Jon Fila is the CEO of Amira Learning",
        evidence_from_ids=[obs_f_id],
        supersedes_ids=[],
        source_quality=0.9,
    )

    adapter = _refine_adapter()
    with _patch_shortlist_all(0.74), _patch_semantic_adapter(adapter):
        async with db_session.begin():
            await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_f_id: obs_f},
            )

    # No conflict rows expected
    result = await db_session.execute(select(MemoryConflict))
    conflicts = list(result.scalars())
    assert len(conflicts) == 0, (
        f"PRECISION: Temporal refinement (REFINE verdict) should not produce conflict "
        f"rows; got {len(conflicts)}"
    )

    result = await db_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_a_id)
    )
    obs_a_after = result.scalar_one()
    assert obs_a_after.superseded_by is None, (
        "PRECISION: obs A should not be superseded by a temporal refinement"
    )


# ── Test 5: fail-safe — no provider → no crash, write succeeds, no supersede ─


@pytest.mark.asyncio
async def test_apply_consolidation_no_provider_fail_safe(
    db_session: AsyncSession,
) -> None:
    """FAIL SAFE: when semantic detector raises NoProviderAvailableError,
    the consolidation write completes normally and no supersede occurs.
    """
    from artemis.providers.resolver import NoProviderAvailableError

    obs_a_id: int
    obs_g_id: int
    obs_g: Observation

    async with db_session.begin():
        obs_a = await write_observation(
            db_session,
            _SCOPE,
            "Jon Fila is the CMO at Amira Learning",
            category="discovery",
            source_quality=0.7,
        )
        obs_a_id = obs_a.id

        obs_g = await write_observation(
            db_session,
            _SCOPE,
            "Amira has 200 employees as of 2025",
            category="discovery",
            source_quality=0.6,
        )
        obs_g_id = obs_g.id

    proposal = ConsolidationProposal(
        category="discovery",
        content="Jon Fila now leads the sales division at Amira Learning",
        evidence_from_ids=[obs_g_id],
        supersedes_ids=[],
        source_quality=0.9,
    )

    with (
        _patch_shortlist_all(0.82),
        patch(
            "artemis.memory.semantic_conflict_detector.resolve_adapter_async",
            new=AsyncMock(side_effect=NoProviderAvailableError("no adapter")),
        ),
        patch(
            "artemis.db.SessionLocal",
            new=MagicMock(
                **{
                    "return_value.__aenter__": AsyncMock(return_value=MagicMock()),
                    "return_value.__aexit__": AsyncMock(return_value=None),
                }
            ),
        ),
    ):
        # Must NOT raise even though LLM provider is unavailable
        async with db_session.begin():
            created = await apply_consolidation(
                db_session,
                _SCOPE,
                [proposal],
                {obs_g_id: obs_g},
            )

    assert len(created) == 1, "New obs must still be written despite no-provider"

    # obs A must NOT be superseded (fail-safe: no-provider → no auto-retire)
    result = await db_session.execute(
        select(MemoryObservation).where(MemoryObservation.id == obs_a_id)
    )
    obs_a_after = result.scalar_one()
    assert obs_a_after.superseded_by is None, (
        "FAIL SAFE: obs A must not be superseded when no LLM provider is available"
    )

    # No semantic conflict rows
    result = await db_session.execute(
        select(MemoryConflict).where(MemoryConflict.conflict_type == "semantic_contradiction")
    )
    sem_rows = list(result.scalars())
    assert len(sem_rows) == 0, "FAIL SAFE: no semantic conflict rows when provider unavailable"
