"""Per-session in-memory cache for the latest MemoryReadEvent.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Only the most-recent MemoryReadEvent per session is retained — no new table,
no persistence across restarts. The backfill endpoint reads from this cache.
"""

from __future__ import annotations

from artemis.floating_artemis.schemas import MemoryReadEvent

# Per-session latest MemoryReadEvent.  session_id → MemoryReadEvent.
_cache: dict[str, MemoryReadEvent] = {}


def put(event: MemoryReadEvent) -> None:
    """Store the latest MemoryReadEvent for the given session."""
    _cache[event.session_id] = event


def get(session_id: str) -> MemoryReadEvent | None:
    """Return the latest MemoryReadEvent for the session, or None."""
    return _cache.get(session_id)


def clear(session_id: str) -> None:
    """Remove any cached event for the session (e.g. on archive)."""
    _cache.pop(session_id, None)
