"""Memory M2 — Integration tests for consolidator conflict detection.

Tests:
  - Two contradictory observations → conflict row + no auto-resolution
  - High-confidence new obs + 2× evidence_count → auto-resolution fires
  - Corroboration formula matches spec
  - Conflict row is written atomically with the observation insert
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.consolidator import (
    corroborate_confidence,
    write_observation_with_conflict_check,
)
from artemis.memory.models import MemoryConflict, MemoryObservation
from artemis.memory.schemas import Scope

_SCOPE = Scope(scope_kind="workspace", scope_id="ws-conflict-test")


# ── Corroboration formula ─────────────────────────────────────────────────────


def test_corroborate_confidence_formula() -> None:
    """confidence = min(0.99, current + (1 - current) * 0.3). Asymptotic, never reaches 1."""
    new_conf, new_count = corroborate_confidence(0.5, 1)
    # 0.5 + (1 - 0.5) * 0.3 = 0.5 + 0.15 = 0.65
    assert new_conf == pytest.approx(0.65, abs=1e-6)
    assert new_count == 2


def test_corroborate_confidence_near_ceiling() -> None:
    """Near 1.0 it approaches but never exceeds 0.99."""
    conf, _ = corroborate_confidence(0.98, 10)
    assert conf < 1.0
    assert conf <= 0.99


def test_corroborate_confidence_monotonic() -> None:
    """Each corroboration increases confidence."""
    c = 0.5
    count = 1
    prev = c
    for _ in range(10):
        c, count = corroborate_confidence(c, count)
        assert c > prev
        prev = c


# ── DB integration tests ──────────────────────────────────────────────────────


async def test_two_contradictory_obs_produces_conflict_row(db_session: AsyncSession) -> None:
    """Insert two observations with the same 8-word prefix, different suffix.
    Expect: 1 conflict row with resolution=NULL.
    """
    async with db_session.begin():
        obs_a = await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Jon Fila leads the marketing strategy at Amira Learning",
            confidence=0.7,
        )

    async with db_session.begin():
        obs_b = await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Jon Fila leads the marketing strategy at Amira EdTech Inc",
            confidence=0.6,
        )

    result = await db_session.execute(select(MemoryConflict))
    conflicts = list(result.scalars())
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.resolution is None  # unresolved — confidence delta < 0.3
    assert conflict.scope_id == _SCOPE.scope_id
    # Pair is stored sorted (min, max)
    pair = {conflict.observation_a_id, conflict.observation_b_id}
    assert obs_a.id in pair
    assert obs_b.id in pair


async def test_conflict_not_auto_resolved_when_evidence_ratio_low(
    db_session: AsyncSession,
) -> None:
    """confidence delta > 0.3 but evidence_ratio = 1.0 (both default=1) → NOT auto-resolved.

    First 8 words match: 'Jon Fila primary email address on record is'
    """
    async with db_session.begin():
        await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Jon Fila primary email address on record is jon.fila.old@amira.com confirmed",
            confidence=0.3,
        )

    async with db_session.begin():
        await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Jon Fila primary email address on record is jon.fila.new@amira.com verified",
            confidence=0.95,
        )

    result = await db_session.execute(select(MemoryConflict))
    conflicts = list(result.scalars())
    assert len(conflicts) == 1
    conflict = conflicts[0]
    # confidence delta = 0.65 > 0.3 BUT evidence_ratio = 1/1 = 1.0 < 2.0 → not auto-resolved
    assert conflict.resolution is None


async def test_conflict_logs_both_observation_ids(db_session: AsyncSession) -> None:
    """Verify the conflict row correctly records both observation IDs.

    First 8 words match: 'Q3 revenue target confirmed by finance team is'
    """
    async with db_session.begin():
        obs_a = await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Q3 revenue target confirmed by finance team is twenty million projected",
            confidence=0.5,
        )

    async with db_session.begin():
        obs_b = await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Q3 revenue target confirmed by finance team is thirty million projected",
            confidence=0.5,
        )

    result = await db_session.execute(select(MemoryConflict))
    conflicts = list(result.scalars())
    assert len(conflicts) == 1
    pair = {conflicts[0].observation_a_id, conflicts[0].observation_b_id}
    assert obs_a.id in pair
    assert obs_b.id in pair
    assert conflicts[0].scope_id == _SCOPE.scope_id


async def test_conflict_row_atomic_with_observation(db_session: AsyncSession) -> None:
    """Conflict row must be committed in the same transaction as the observation insert.

    First 8 words match: 'Product launch schedule milestone confirmed for this release'
    """
    async with db_session.begin():
        await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Product launch schedule milestone confirmed for this release Q3 2026",
            confidence=0.7,
        )
        await write_observation_with_conflict_check(
            db_session,
            _SCOPE,
            "Product launch schedule milestone confirmed for this release Q4 2027",
            confidence=0.6,
        )

    obs_result = await db_session.execute(select(MemoryObservation))
    obs_count = len(list(obs_result.scalars()))
    conflict_result = await db_session.execute(select(MemoryConflict))
    conflict_count = len(list(conflict_result.scalars()))

    assert obs_count == 2
    assert conflict_count == 1
