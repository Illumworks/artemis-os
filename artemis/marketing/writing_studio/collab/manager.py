"""Per-draft collaborative editing connection registry.

Tracks which WebSocket connections are active in each draft "room" and records
the identity and client-id of each participant so Phase 1 can render a
presence roster and route peer.selection broadcasts.

Thread/task safety: single-process asyncio server; no cross-process fan-out.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from artemis.identity.dependencies import RequestIdentity

logger = logging.getLogger(__name__)


@dataclass
class _Peer:
    """Per-connection record stored in a room."""

    client_id: str
    identity: RequestIdentity


class CollabManager:
    """Tracks active collab connections, organised by room (str(draft_id))."""

    def __init__(self) -> None:
        # room -> {WebSocket: _Peer}
        self._rooms: dict[str, dict[WebSocket, _Peer]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, room: str, websocket: WebSocket, identity: RequestIdentity) -> str:
        """Accept *websocket*, record its *identity*, add to *room*, return clientId."""
        await websocket.accept()
        client_id = uuid.uuid4().hex
        self._rooms.setdefault(room, {})[websocket] = _Peer(client_id=client_id, identity=identity)
        logger.debug(
            "collab:connect room=%s user=%s client=%s total=%d",
            room,
            identity.email,
            client_id,
            len(self._rooms[room]),
        )
        return client_id

    def disconnect(self, room: str, websocket: WebSocket) -> str | None:
        """Remove *websocket* from *room*; return the departing clientId (or None).

        Callers use the returned clientId to broadcast ``presence.leave``.
        Cleans up empty rooms.
        """
        if room not in self._rooms:
            return None
        peer = self._rooms[room].pop(websocket, None)
        if not self._rooms[room]:
            del self._rooms[room]
            logger.debug("collab:room_empty room=%s", room)
        if peer is None:
            return None
        logger.debug(
            "collab:disconnect room=%s client=%s",
            room,
            peer.client_id,
        )
        return peer.client_id

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

        # Guard: disconnect() may have already cleaned up this room while we
        # were awaiting send_json() above.  Use .get() so a concurrent removal
        # is a safe no-op rather than a KeyError.
        bucket = self._rooms.get(room)
        if bucket is not None:
            for d in dead:
                bucket.pop(d, None)
        if room in self._rooms and not self._rooms[room]:
            del self._rooms[room]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def room_count(self, room: str) -> int:
        """Return the number of active connections in *room*."""
        return len(self._rooms.get(room, {}))

    def roster(self, room: str) -> list[RequestIdentity]:
        """Return the list of identities currently connected to *room*."""
        return [p.identity for p in self._rooms.get(room, {}).values()]

    def peers(self, room: str, exclude: WebSocket | None = None) -> list[_Peer]:
        """Return all _Peer records in *room*, optionally excluding one WebSocket."""
        return [peer for ws, peer in self._rooms.get(room, {}).items() if ws is not exclude]

    def client_id_for(self, room: str, websocket: WebSocket) -> str | None:
        """Return the clientId assigned to *websocket* in *room*, or None."""
        peer = self._rooms.get(room, {}).get(websocket)
        return peer.client_id if peer is not None else None


# Module-level singleton — import this everywhere instead of constructing
# a new manager per request.
collab_manager = CollabManager()
