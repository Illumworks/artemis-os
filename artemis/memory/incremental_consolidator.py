"""Phase B3: Incremental consolidation trigger.

Tracks write counts per scope+category. When a scope crosses the threshold,
schedules a consolidation run after a debounce delay. The debounce ensures a
burst of rapid writes triggers only one consolidation job.

Design: synchronous notification API (no await) so write_drawer can call it without
restructuring. The async consolidation work is launched via asyncio.ensure_future.

Usage:
    consolidator = get_incremental_consolidator()
    consolidator.notify_drawer_written(scope, category="discovery")
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from artemis.memory.schemas import Scope

_logger = logging.getLogger(__name__)

_DEFAULT_THRESHOLD = 25
_DEFAULT_DEBOUNCE_SECONDS = 120.0


@dataclass
class _SlotKey:
    scope_kind: str
    scope_id: str
    category: str

    def __hash__(self) -> int:
        return hash((self.scope_kind, self.scope_id, self.category))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _SlotKey):
            return NotImplemented
        return (
            self.scope_kind == other.scope_kind
            and self.scope_id == other.scope_id
            and self.category == other.category
        )


class IncrementalConsolidator:
    """Tracks write counts and fires debounced consolidation jobs.

    Thread-safety: designed for use within a single asyncio event loop.
    All state mutation happens synchronously (no await) so it's safe to
    call from sync or async contexts.
    """

    def __init__(
        self,
        *,
        threshold: int = _DEFAULT_THRESHOLD,
        debounce_seconds: float = _DEFAULT_DEBOUNCE_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._threshold = threshold
        self._debounce_seconds = debounce_seconds
        self._enabled = enabled
        # Write counters per slot
        self._counts: dict[_SlotKey, int] = {}
        # Pending debounce handles per slot
        self._timers: dict[_SlotKey, asyncio.TimerHandle] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def notify_drawer_written(self, scope: Scope, category: str = "discovery") -> None:
        """Increment the write counter for this scope+category.

        If the count crosses the threshold, schedule a debounced consolidation run.
        Safe to call from sync code — does not block, does not await.
        """
        if not self._enabled:
            return
        key = _SlotKey(scope.scope_kind, scope.scope_id, category)
        self._counts[key] = self._counts.get(key, 0) + 1
        if self._counts[key] >= self._threshold:
            self._schedule_or_reset(key)

    def get_count(self, scope: Scope, category: str = "discovery") -> int:
        """Return the current write counter for a slot (test helper)."""
        key = _SlotKey(scope.scope_kind, scope.scope_id, category)
        return self._counts.get(key, 0)

    def reset_count(self, scope: Scope, category: str = "discovery") -> None:
        """Reset counter for a slot without cancelling a pending timer (test helper)."""
        key = _SlotKey(scope.scope_kind, scope.scope_id, category)
        self._counts.pop(key, None)

    def cancel_pending(self, scope: Scope, category: str = "discovery") -> None:
        """Cancel a pending debounce timer (test helper / graceful shutdown)."""
        key = _SlotKey(scope.scope_kind, scope.scope_id, category)
        self._cancel_timer(key)

    def pending_slots(self) -> list[tuple[str, str, str]]:
        """Return list of (scope_kind, scope_id, category) with pending timers."""
        return [(k.scope_kind, k.scope_id, k.category) for k in self._timers]

    # ── Internals ─────────────────────────────────────────────────────────────

    def _schedule_or_reset(self, key: _SlotKey) -> None:
        """Cancel any existing debounce timer and start a fresh one."""
        self._cancel_timer(key)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — skip scheduling (e.g., in sync test environments)
            return
        def _fire(k: _SlotKey = key) -> asyncio.Task[None]:
            return asyncio.ensure_future(self._run_consolidation(k))

        handle = loop.call_later(self._debounce_seconds, _fire)
        self._timers[key] = handle

    def _cancel_timer(self, key: _SlotKey) -> None:
        handle = self._timers.pop(key, None)
        if handle is not None:
            handle.cancel()

    async def _run_consolidation(self, key: _SlotKey) -> None:
        """The actual consolidation job, run after the debounce delay."""
        self._timers.pop(key, None)
        # Reset count before running so writes during consolidation start fresh
        self._counts[key] = 0

        _logger.info(
            "Incremental consolidation: %s/%s category=%s",
            key.scope_kind,
            key.scope_id,
            key.category,
        )

        try:
            from artemis.db import SessionLocal
            from artemis.memory.consolidator import (
                apply_consolidation,
                consolidate_observations,
            )
            from artemis.memory.retrieval import search_observations
            from artemis.memory.schemas import Observation, Scope

            scope = Scope(scope_kind=key.scope_kind, scope_id=key.scope_id)

            async with SessionLocal() as session:
                # Pull recent observations for this scope+category
                candidates = await search_observations(
                    session,
                    [scope],
                    "",  # empty query → recency only
                    limit=50,
                    modes=["recency"],
                )
                # ScoredObservation extends the observation shape with rank
                # metadata; collapse back to plain Observation for the
                # consolidator API.
                filtered: list[Observation] = [
                    Observation.model_validate(c, from_attributes=True)
                    for c in candidates
                    if c.category == key.category
                ]
                source_map = {obs.id: obs for obs in filtered}

                proposals = await consolidate_observations(filtered)
                if proposals:
                    async with session.begin():
                        await apply_consolidation(session, scope, proposals, source_map)
                    _logger.info(
                        "Consolidation applied: %d proposals for %s/%s",
                        len(proposals),
                        key.scope_kind,
                        key.scope_id,
                    )
        except Exception:
            _logger.exception(
                "Incremental consolidation failed for %s/%s category=%s",
                key.scope_kind,
                key.scope_id,
                key.category,
            )


# ── Module-level singleton ────────────────────────────────────────────────────

_singleton: IncrementalConsolidator | None = None


def get_incremental_consolidator() -> IncrementalConsolidator:
    global _singleton
    if _singleton is None:
        _singleton = IncrementalConsolidator()
    return _singleton


def _reset_singleton_for_tests() -> None:
    """Replace the module singleton with a fresh instance (test helper only)."""
    global _singleton
    _singleton = None
