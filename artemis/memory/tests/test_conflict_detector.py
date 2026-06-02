"""Memory M2 — Tests for the pure conflict detector.

9 tests covering 3 detector functions × (positive, negative, edge) cases.
No DB access — all pure Python.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from artemis.memory.conflict_detector import (
    _detect_incompatible_relational,
    _detect_incompatible_temporal,
    _detect_incompatible_values,
    _windows_overlap,
    detect_conflicts,
)
from artemis.memory.schemas import Observation

_NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
_PAST = _NOW - timedelta(days=30)
_FUTURE = _NOW + timedelta(days=30)


def _obs(
    obs_id: int,
    content: str,
    scope_kind: str = "workspace",
    scope_id: str = "ws-test",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    superseded_by: int | None = None,
    confidence: float = 0.5,
    evidence_count: int = 1,
) -> Observation:
    return Observation(
        id=obs_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        category="discovery",
        content=content,
        content_hash=f"hash-{obs_id}",
        score=1.0,
        hit_count=0,
        source_quality=0.5,
        user_confirmed=False,
        valid_from=valid_from,
        valid_until=valid_until,
        superseded_by=superseded_by,
        owner_user_id=None,
        created_at=_NOW,
        accessed_at=_NOW,
        confidence=confidence,
        supersedes=None,
        evidence_count=evidence_count,
    )


# ── _detect_incompatible_values ───────────────────────────────────────────────


def test_incompatible_values_positive() -> None:
    """Same 8-word prefix, different suffix, overlapping windows → conflict."""
    new = _obs(2, "Jon Fila is the Chief Marketing Officer at Amira Learning")
    existing = _obs(1, "Jon Fila is the Chief Marketing Officer at Amira EdTech")
    results = _detect_incompatible_values(new, [existing])
    assert len(results) == 1
    assert results[0].existing_id == 1
    assert results[0].conflict_type == "incompatible_values"


def test_incompatible_values_negative_different_prefix() -> None:
    """Different prefix → NOT an incompatible_values conflict."""
    new = _obs(2, "The campaign launches next quarter")
    existing = _obs(1, "Jon Fila is the Chief Marketing Officer at Amira Learning")
    results = _detect_incompatible_values(new, [existing])
    assert results == []


def test_incompatible_values_edge_non_overlapping_windows() -> None:
    """Same prefix, different value, but non-overlapping windows → no conflict."""
    new = _obs(2, "Jon Fila is the VP of Sales effective January 2027", valid_from=_FUTURE)
    existing = _obs(
        1,
        "Jon Fila is the VP of Finance effective January 2026",
        valid_from=_PAST,
        valid_until=_NOW - timedelta(days=1),
    )
    results = _detect_incompatible_values(new, [existing])
    assert results == []


# ── _detect_incompatible_temporal ────────────────────────────────────────────


def test_incompatible_temporal_positive() -> None:
    """New claim ends before existing starts (gap) with matching prefix → temporal conflict."""
    new = _obs(
        2,
        "Campaign budget was approved last quarter",
        valid_from=_PAST - timedelta(days=60),
        valid_until=_PAST - timedelta(days=30),
    )
    existing = _obs(
        1,
        "Campaign budget was approved this quarter",
        valid_from=_NOW,
        valid_until=None,
    )
    results = _detect_incompatible_temporal(new, [existing])
    assert len(results) == 1
    assert results[0].conflict_type == "incompatible_temporal"


def test_incompatible_temporal_negative_overlapping() -> None:
    """Overlapping windows should NOT produce a temporal conflict (incompatible_values handles it)."""
    new = _obs(2, "Campaign budget allocated for Q2", valid_from=_PAST, valid_until=_FUTURE)
    existing = _obs(1, "Campaign budget allocated for Q3", valid_from=_NOW, valid_until=_FUTURE)
    results = _detect_incompatible_temporal(new, [existing])
    assert results == []  # Windows overlap, so no temporal gap conflict


def test_incompatible_temporal_edge_both_open() -> None:
    """Two observations with both valid_from=None, valid_until=None → no temporal conflict."""
    new = _obs(2, "Budget approved for campaigns", valid_from=None, valid_until=None)
    existing = _obs(1, "Budget approved for marketing", valid_from=None, valid_until=None)
    results = _detect_incompatible_temporal(new, [existing])
    assert results == []


# ── _detect_incompatible_relational ──────────────────────────────────────────


def test_incompatible_relational_positive() -> None:
    """Positive RELATES_TO vs NOT_RELATES_TO with overlapping windows → conflict."""
    new = _obs(2, "entity_X RELATES_TO entity_Y via partnership")
    existing = _obs(1, "entity_X NOT_RELATES_TO entity_Y")
    results = _detect_incompatible_relational(new, [existing])
    assert len(results) == 1
    assert results[0].conflict_type == "incompatible_relational"


def test_incompatible_relational_negative_both_positive() -> None:
    """Two positive RELATES_TO claims → NOT a relational conflict."""
    new = _obs(2, "entity_X RELATES_TO entity_Y as partner")
    existing = _obs(1, "entity_X RELATES_TO entity_Y as vendor")
    results = _detect_incompatible_relational(new, [existing])
    assert results == []


def test_incompatible_relational_edge_non_overlapping_window() -> None:
    """RELATES_TO vs NOT_RELATES_TO but non-overlapping windows → no conflict."""
    new = _obs(
        2,
        "entity_X RELATES_TO entity_Y",
        valid_from=_FUTURE,
        valid_until=None,
    )
    existing = _obs(
        1,
        "entity_X NOT_RELATES_TO entity_Y",
        valid_from=_PAST,
        valid_until=_NOW - timedelta(days=1),
    )
    results = _detect_incompatible_relational(new, [existing])
    assert results == []


# ── detect_conflicts (composite) ─────────────────────────────────────────────


def test_detect_conflicts_deduplicates() -> None:
    """A single existing observation matching multiple detectors yields one candidate."""
    new = _obs(2, "Jon Fila is the Chief Marketing Officer RELATES_TO Amira Learning")
    existing = _obs(1, "Jon Fila is the Chief Marketing Officer NOT_RELATES_TO Amira Learning")
    results = detect_conflicts(new, [existing])
    # Each candidate is deduplicated by existing_id
    existing_ids = [c.existing_id for c in results]
    assert existing_ids.count(1) == 1


def test_detect_conflicts_skips_superseded() -> None:
    """Already-superseded observations are not reported as conflicts."""
    new = _obs(3, "Jon Fila is the Chief Revenue Officer at Amira")
    existing = _obs(1, "Jon Fila is the Chief Revenue Strategy Officer at Amira", superseded_by=2)
    results = detect_conflicts(new, [existing])
    assert results == []


def test_windows_overlap_helper() -> None:
    """Sanity-check _windows_overlap for open/closed combinations."""
    # Both open → overlap
    assert _windows_overlap(None, None, None, None)
    # A ends before B starts → no overlap
    assert not _windows_overlap(_PAST, _NOW - timedelta(days=1), _NOW, _FUTURE)
    # A open-ended, B starts in future → overlap (A still running)
    assert _windows_overlap(_PAST, None, _FUTURE, None)
