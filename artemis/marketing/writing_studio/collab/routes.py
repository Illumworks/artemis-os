"""WebSocket endpoint for real-time collaborative editing of writing drafts.

This router deliberately has NO HTTP-level auth dependencies — the main
writing_studio router applies `require_token` which would break the
CF-Access-on-upgrade flow.  Identity is resolved manually per-connection.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from artemis.marketing.writing_studio.collab.auth import resolve_ws_identity
from artemis.marketing.writing_studio.collab.manager import collab_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writing-studio", tags=["writing-studio-collab"])


def _peer_dict(client_id: str, identity_email: str, identity_name: str | None) -> dict[str, str]:
    return {"clientId": client_id, "email": identity_email, "name": identity_name or ""}


@router.websocket("/drafts/{draft_id}/collab")
async def collab_ws(websocket: WebSocket, draft_id: int) -> None:
    """Per-draft collab WebSocket.

    Phase 1: full presence roster protocol — presence.init on join,
    presence.join / presence.leave broadcasts, and peer.selection relaying.
    """
    identity = await resolve_ws_identity(websocket)
    if identity is None:
        # Close before accept() — identity is untrusted.
        await websocket.close(code=4401)
        return

    room = str(draft_id)

    # connect() calls websocket.accept() and returns the new clientId.
    client_id = await collab_manager.connect(room, websocket, identity)

    try:
        # 1. Send presence.init to the JOINING socket only (peers = everyone else).
        existing_peers = collab_manager.peers(room, exclude=websocket)
        await websocket.send_json(
            {
                "type": "presence.init",
                "you": _peer_dict(client_id, identity.email, identity.name),
                "peers": [
                    _peer_dict(p.client_id, p.identity.email, p.identity.name)
                    for p in existing_peers
                ],
            }
        )

        # 2. Broadcast presence.join to OTHERS (exclude the joiner).
        await collab_manager.broadcast(
            room,
            {
                "type": "presence.join",
                "peer": _peer_dict(client_id, identity.email, identity.name),
            },
            exclude=websocket,
        )

        # 3. Receive loop.
        while True:
            data = await websocket.receive_text()

            # Non-JSON frames (e.g. 'ping' keepalive) are ignored harmlessly.
            try:
                msg = json.loads(data)
            except (json.JSONDecodeError, ValueError):
                continue

            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type")

            if msg_type == "selection":
                # Relay peer.selection to everyone EXCEPT the sender.
                sender_client_id = collab_manager.client_id_for(room, websocket)
                if sender_client_id is None:
                    continue
                try:
                    from_pos = int(msg["from"])
                    to_pos = int(msg["to"])
                except (KeyError, TypeError, ValueError):
                    continue
                await collab_manager.broadcast(
                    room,
                    {
                        "type": "peer.selection",
                        "clientId": sender_client_id,
                        "from": from_pos,
                        "to": to_pos,
                    },
                    exclude=websocket,
                )
            # Unknown type — ignore defensively.

    except WebSocketDisconnect:
        pass
    finally:
        departing_client_id = collab_manager.disconnect(room, websocket)
        if departing_client_id is not None:
            await collab_manager.broadcast(
                room,
                {"type": "presence.leave", "clientId": departing_client_id},
            )
