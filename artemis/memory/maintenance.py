"""Phase B3: Score decay maintenance.

Applies time-based score decay to memory_observations. Category-aware decay
factors reduce the stored `score` field so that stale observations rank lower
in retrieval without being deleted (lossless rule preserved).

Decay model: score *= category_factor per maintenance run.
Typical schedule: daily cron or on-demand via POST /api/memory/maintain.

Category decay factors (per-run):
    warning:     1.00  — never decays (time-sensitive signals)
    convention:  0.99  — very slow decay
    decision:    0.97  — moderate decay
    discovery:   0.93  — faster decay (raw observations age out)
    <default>:   0.95  — unknown categories
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.memory.models import MemoryObservation

_logger = logging.getLogger(__name__)

# Category → per-run decay multiplier
_DECAY_FACTORS: dict[str, float] = {
    "warning": 1.00,
    "convention": 0.99,
    "decision": 0.97,
    "discovery": 0.93,
    "commitment": 1.00,
    # Argus district-research findings: durable reference data, decays slowly
    # (gently fades to prompt re-research rather than going stale silently).
    "district_research": 0.99,
}
_DEFAULT_DECAY = 0.95

# Public set of recognised categories — imported by store.write_observation for
# write-time validation. Keep in sync with _DECAY_FACTORS above (single source).
KNOWN_CATEGORIES: frozenset[str] = frozenset(_DECAY_FACTORS.keys())


def _decay_for(category: str) -> float:
    return _DECAY_FACTORS.get(category, _DEFAULT_DECAY)


async def run_maintenance(session: AsyncSession) -> dict[str, int]:
    """Apply score decay to all active (non-superseded) observations, then
    collapse near-duplicate clones (M1b, lossless + precision-safe — see
    artemis.memory.near_duplicate).

    Runs inside the caller-managed transaction. Returns a dict mapping
    category name to number of rows updated, plus near_duplicate_clusters /
    near_duplicate_superseded counters from the clone-consolidation pass.
    """
    as_of = datetime.now(UTC)
    _logger.info("Memory maintenance starting at %s", as_of.isoformat())

    updated: dict[str, int] = {}

    for category, factor in _DECAY_FACTORS.items():
        if factor == 1.0:
            updated[category] = 0
            continue
        result = await session.execute(
            update(MemoryObservation)
            .where(
                MemoryObservation.category == category,
                MemoryObservation.superseded_by.is_(None),
            )
            .values(score=MemoryObservation.score * factor)
        )
        updated[category] = result.rowcount  # type: ignore[attr-defined]
        _logger.debug(
            "Decayed %d '%s' observations by factor %.3f",
            updated[category],
            category,
            factor,
        )

    # Decay any unknown categories with the default factor
    result = await session.execute(
        update(MemoryObservation)
        .where(
            MemoryObservation.category.notin_(list(_DECAY_FACTORS.keys())),
            MemoryObservation.superseded_by.is_(None),
        )
        .values(score=MemoryObservation.score * _DEFAULT_DECAY)
    )
    if result.rowcount:  # type: ignore[attr-defined]
        updated["other"] = result.rowcount  # type: ignore[attr-defined]
        _logger.debug(
            "Decayed %d unknown-category observations by factor %.3f",
            updated["other"],
            _DEFAULT_DECAY,
        )

    # ── M1b: near-duplicate (clone) consolidation ─────────────────────────────
    # Collapses byte-identical-after-normalization clone clusters losslessly
    # (superseded_by + evidence links; nothing deleted). Runs in a SAVEPOINT so
    # a failure here can never roll back the score decay above or abort the
    # caller's transaction.
    try:
        from artemis.memory.near_duplicate import consolidate_near_duplicates

        async with session.begin_nested():
            stats = await consolidate_near_duplicates(session)
        updated["near_duplicate_clusters"] = stats.clusters
        updated["near_duplicate_superseded"] = stats.observations_superseded
        if stats.clusters:
            _logger.info(
                "Near-duplicate consolidation: %d clusters, %d observations superseded",
                stats.clusters,
                stats.observations_superseded,
            )
    except Exception:
        _logger.exception("Near-duplicate consolidation failed (non-fatal); decay still applies")

    _logger.info("Memory maintenance complete: %s", updated)
    return updated
