"""WebSocket endpoint for real-time collaborative editing of writing drafts.

This router deliberately has NO HTTP-level auth dependencies — the main
writing_studio router applies `require_token` which would break the
CF-Access-on-upgrade flow.  Identity is resolved manually per-connection.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from artemis.marketing.writing_studio.collab.auth import resolve_ws_identity
from artemis.marketing.writing_studio.collab.manager import collab_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writing-studio", tags=["writing-studio-collab"])


@router.websocket("/drafts/{draft_id}/collab")
async def collab_ws(websocket: WebSocket, draft_id: int) -> None:
    """Per-draft collab WebSocket.

    Phase 0: identity verification, heartbeat keep-alive, and presence
    broadcasting (room count).  No editor synchronisation yet — that lands
    in Phase 1 (OT deltas) and Phase 2 (soft-lock / version CAS).
    """
    identity = await resolve_ws_identity(websocket)
    if identity is None:
        # Close before accept() — identity is untrusted.
        await websocket.close(code=4401)
        return

    room = str(draft_id)
    await collab_manager.connect(room, websocket, identity)
    try:
        # Announce the updated room size to everyone (including the new joiner).
        await collab_manager.broadcast(
            room,
            {"type": "collab.presence", "count": collab_manager.room_count(room)},
        )
        while True:
            # Heartbeat frames are accepted but the payload is ignored in Phase 0.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await collab_manager.disconnect(room, websocket)
        await collab_manager.broadcast(
            room,
            {"type": "collab.presence", "count": collab_manager.room_count(room)},
        )
