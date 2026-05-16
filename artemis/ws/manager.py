"""WebSocket connection registry and broadcast room manager.

A "room" corresponds to a run ID (agent_run_id or workflow_run_id).
All clients subscribed to the same room receive the same broadcast.

Thread/task safety: this module is designed for a single-process asyncio
server. There is no cross-process fan-out (Redis pub/sub etc.) in V1.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class _WSProto(Protocol):
    """Structural interface satisfied by both FastAPI's WebSocket and test fakes."""

    async def accept(self) -> None: ...

    async def send_json(self, data: Any) -> None: ...


class WebSocketManager:
    """Tracks active WS connections, organised by room (run_id)."""

    def __init__(self) -> None:
        self._rooms: dict[str, set[_WSProto]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self, room: str, websocket: _WSProto) -> None:
        """Accept *websocket* and add it to *room*."""
        await websocket.accept()
        self._rooms.setdefault(room, set()).add(websocket)
        logger.debug("ws:connect room=%s total=%d", room, len(self._rooms[room]))

    async def disconnect(self, room: str, websocket: _WSProto) -> None:
        """Remove *websocket* from *room*; clean up empty rooms."""
        if room in self._rooms:
            self._rooms[room].discard(websocket)
            if not self._rooms[room]:
                del self._rooms[room]
                logger.debug("ws:room_empty room=%s", room)

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(self, room: str, event: dict[str, Any]) -> None:
        """Send *event* as JSON to every WebSocket in *room*.

        Dead connections are silently removed so they never block live ones.
        """
        if room not in self._rooms:
            return

        dead: list[_WSProto] = []
        for ws in list(self._rooms[room]):
            try:
                await ws.send_json(event)
            except Exception:
                logger.debug("ws:send_failed room=%s — marking dead", room)
                dead.append(ws)

        for d in dead:
            self._rooms[room].discard(d)
        if room in self._rooms and not self._rooms[room]:
            del self._rooms[room]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def room_count(self, room: str) -> int:
        """Return the number of active connections in *room*."""
        return len(self._rooms.get(room, set()))


# Module-level singleton — import this everywhere instead of constructing
# a new manager per request.
ws_manager = WebSocketManager()
