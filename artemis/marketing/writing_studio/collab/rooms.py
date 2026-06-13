"""Per-draft authoritative step log for prosemirror-collab live text sync.

`manager.py` tracks *who* is connected (presence). This module tracks the
*document coordination* state that makes prosemirror-collab converge:

  * a monotonic ``version`` (number of committed steps),
  * the ordered step log (each step tagged with the collab clientID that
    authored it — so ``receiveTransaction`` can confirm a client's own steps),
  * a baseline text snapshot (the doc at version 0, hydrated once from
    ``live_content``) used to initialise / re-sync joining clients,
  * a per-room ``asyncio.Lock`` — the per-draft serialization point for
    concurrent step submission required by the design (R-step-ordering).

The room is the single *writer* to ``live_content`` while clients are
connected, but Python has no ProseMirror, so it cannot itself materialise the
doc to text. Instead clients send a debounced ``materialize {version, text}``
and the room persists the converged text (gated on version) — see the route
handler. The room object here stays pure/synchronous and DB-free so the
ordering logic is trivially testable; DB access lives in the route.

Single-process, in-memory. Multi-worker fan-out is explicitly out of scope for
v1 (pin collab WS to one worker — R10). On server restart / empty-room drop the
log is lost and clients re-init from ``live_content`` (R11, acceptable — same
exposure as today's autosave debounce).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _StepRecord:
    """One committed ProseMirror step + the collab clientID that authored it."""

    step: dict[str, Any]
    client_id: str


@dataclass
class DraftRoom:
    """Authoritative step-ordering state for one draft room."""

    base_text: str = ""
    base_version: int = 0
    steps: list[_StepRecord] = field(default_factory=list)
    # Highest version persisted to live_content; -1 = nothing flushed yet.
    last_flushed_version: int = -1
    hydrated: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def version(self) -> int:
        """Current authoritative version = baseline + committed step count."""
        return self.base_version + len(self.steps)

    def hydrate(self, text: str) -> None:
        """Seed the baseline doc (version 0) from ``live_content``. Idempotent-guarded
        by the caller via ``hydrated``."""
        self.base_text = text
        self.base_version = 0
        self.steps = []
        self.last_flushed_version = -1
        self.hydrated = True

    def append_steps(self, steps: list[dict[str, Any]], client_id: str) -> int:
        """Append *steps* (raw step JSON) authored by *client_id*; return the base
        version they were applied at (i.e. the version BEFORE appending)."""
        base = self.version
        for raw in steps:
            self.steps.append(_StepRecord(step=raw, client_id=client_id))
        return base

    def serialized_steps_since(self, version: int) -> list[dict[str, Any]]:
        """Wire form of every step that took the doc past *version*.

        Each entry is ``{"step": <stepJSON>, "clientID": <id>}`` so the client
        can feed ``receiveTransaction(steps, clientIDs)`` directly.
        """
        idx = max(0, version - self.base_version)
        return [{"step": r.step, "clientID": r.client_id} for r in self.steps[idx:]]

    def init_payload(self) -> dict[str, Any]:
        """`collab.init` for a (re)joining client: the baseline doc at
        ``base_version`` plus all steps since, so the client rebuilds and replays
        to the current authoritative version. The room is authoritative — the
        client adopts this and discards whatever it loaded over HTTP."""
        return {
            "type": "collab.init",
            "version": self.base_version,
            "doc": self.base_text,
            "steps": self.serialized_steps_since(self.base_version),
            "currentVersion": self.version,
        }

    def should_flush(self, version: int) -> bool:
        """True when a client-offered materialization at *version* should be
        persisted: it must match a real committed version and be newer than what
        we've already flushed (idempotent across redundant offers from peers)."""
        return self.base_version <= version <= self.version and version > self.last_flushed_version

    def mark_flushed(self, version: int) -> None:
        self.last_flushed_version = version


class CollabRoomRegistry:
    """In-memory registry of per-draft :class:`DraftRoom` objects."""

    def __init__(self) -> None:
        self._rooms: dict[str, DraftRoom] = {}

    def get(self, room: str) -> DraftRoom:
        """Return (creating if needed) the room for *room* (= str(draft_id))."""
        return self._rooms.setdefault(room, DraftRoom())

    def peek(self, room: str) -> DraftRoom | None:
        return self._rooms.get(room)

    def drop(self, room: str) -> None:
        """Discard the room (called when the last connection leaves)."""
        self._rooms.pop(room, None)

    def clear(self) -> None:
        """Test hook — wipe all rooms."""
        self._rooms.clear()


# Module-level singleton — import this everywhere.
room_registry = CollabRoomRegistry()
