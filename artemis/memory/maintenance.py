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
}
_DEFAULT_DECAY = 0.95

# Public set of recognised categories — imported by store.write_observation for
# write-time validation. Keep in sync with _DECAY_FACTORS above (single source).
KNOWN_CATEGORIES: frozenset[str] = frozenset(_DECAY_FACTORS.keys())


def _decay_for(category: str) -> float:
    return _DECAY_FACTORS.get(category, _DEFAULT_DECAY)


async def run_maintenance(session: AsyncSession) -> dict[str, int]:
    """Apply score decay to all active (non-superseded) observations.

    Runs inside the caller-managed transaction. Returns a dict mapping
    category name to number of rows updated.
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

    _logger.info("Memory maintenance complete: %s", updated)
    return updated
