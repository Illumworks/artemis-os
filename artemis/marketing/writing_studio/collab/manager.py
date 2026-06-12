"""Per-draft collaborative editing connection registry.

Tracks which WebSocket connections are active in each draft "room" and records
the identity of each participant so Phase 1 can render a presence roster.

Thread/task safety: single-process asyncio server; no cross-process fan-out.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

from artemis.identity.dependencies import RequestIdentity

logger = logging.getLogger(__name__)


class CollabManager:
    """Tracks active collab connections, organised by room (str(draft_id))."""

    def __init__(self) -> None:
        # room -> {WebSocket: RequestIdentity}
        self._rooms: dict[str, dict[WebSocket, RequestIdentity]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, room: str, websocket: WebSocket, identity: RequestIdentity) -> None:
        """Accept *websocket*, record its *identity*, and add to *room*."""
        await websocket.accept()
        self._rooms.setdefault(room, {})[websocket] = identity
        logger.debug(
            "collab:connect room=%s user=%s total=%d",
            room,
            identity.email,
            len(self._rooms[room]),
        )

    async def disconnect(self, room: str, websocket: WebSocket) -> None:
        """Remove *websocket* from *room*; clean up empty rooms."""
        if room in self._rooms:
            self._rooms[room].pop(websocket, None)
            if not self._rooms[room]:
                del self._rooms[room]
                logger.debug("collab:room_empty room=%s", room)

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(
        self,
        room: str,
        event: dict[str, Any],
        exclude: WebSocket | None = None,
    ) -> None:
        """Send *event* as JSON to every WebSocket in *room* (except *exclude*).

        Dead connections are silently removed so they never block live ones.
        """
        if room not in self._rooms:
            return

        dead: list[WebSocket] = []
        for ws in list(self._rooms[room]):
            if ws is exclude:
                continue
            try:
                await ws.send_json(event)
            except Exception:
                logger.debug("collab:send_failed room=%s — marking dead", room)
                dead.append(ws)

        for d in dead:
            self._rooms[room].pop(d, None)
        if room in self._rooms and not self._rooms[room]:
            del self._rooms[room]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def room_count(self, room: str) -> int:
        """Return the number of active connections in *room*."""
        return len(self._rooms.get(room, {}))

    def roster(self, room: str) -> list[RequestIdentity]:
        """Return the list of identities currently connected to *room*.

        Phase 1 will use this to render presence avatars.
        """
        return list(self._rooms.get(room, {}).values())


# Module-level singleton — import this everywhere instead of constructing
# a new manager per request.
collab_manager = CollabManager()
