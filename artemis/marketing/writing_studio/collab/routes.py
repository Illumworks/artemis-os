"""WebSocket endpoint for real-time collaborative editing of writing drafts.

This router deliberately has NO HTTP-level auth dependencies — the main
writing_studio router applies `require_token` which would break the
CF-Access-on-upgrade flow.  Identity is resolved manually per-connection.

Phase 1 (presence): presence.init / presence.join / presence.leave +
peer.selection relaying.

Phase 3 (live text sync, prosemirror-collab):
  * The collab baseline (authoritative doc + version + step log) rides inside
    presence.init under the ``collab`` key, so a (re)joining client can adopt
    the room's state and start the collab plugin at the right version.
  * Client → ``steps {version, steps, clientID}`` submits collab steps; the
    room (per-draft serialization point) version-checks, appends, and
    broadcasts ``collab.steps`` to ALL clients (incl. origin, which confirms
    its own steps via the collab clientID).
  * Client → ``materialize {version, text}`` offers the converged text; the
    room is the single writer to ``live_content`` (version-gated, idempotent
    across peers) and broadcasts ``collab.flushed`` so the HTTP fallback's
    compare-and-set counter stays accurate.
  * Save-version (HTTP) calls :func:`broadcast_version_rebase` to snap all
    editors to the newly-committed version.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from artemis import db as _db
from artemis.marketing.writing_studio.collab.auth import resolve_ws_identity
from artemis.marketing.writing_studio.collab.manager import collab_manager
from artemis.marketing.writing_studio.collab.rooms import DraftRoom, room_registry
from artemis.marketing.writing_studio.live_content import get_live_text, set_live_content

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/writing-studio", tags=["writing-studio-collab"])


def _peer_dict(client_id: str, identity_email: str, identity_name: str | None) -> dict[str, str]:
    return {"clientId": client_id, "email": identity_email, "name": identity_name or ""}


async def _hydrate_room(room: DraftRoom, draft_id: int) -> dict[str, Any]:
    """Ensure *room* has a baseline (hydrating from live_content on first use);
    return the ``collab`` baseline payload for presence.init. Guarded by the
    room lock so concurrent joins don't double-hydrate."""
    async with room.lock:
        if not room.hydrated:
            # Late-bound (db.SessionLocal read at call time) so tests can inject a
            # NullPool factory — the WS handler opens its own session, and sync
            # TestClient WS tests use ephemeral event loops a pooled engine can't
            # safely span.
            async with _db.SessionLocal() as session:
                text = await get_live_text(session, draft_id)
            room.hydrate(text)
        return room.init_payload()


async def _handle_steps(
    room: DraftRoom, room_key: str, websocket: WebSocket, msg: dict[str, Any]
) -> None:
    """Order and broadcast a client's collab steps (the heart of Phase 3)."""
    raw_steps = msg.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return
    try:
        base_version = int(msg["version"])
    except (KeyError, TypeError, ValueError):
        return
    # Attribution uses the prosemirror-collab clientID FROM THE MESSAGE (a
    # client-generated id), NOT the server's presence clientId — receiveTransaction
    # recognises a client's own steps by this id and confirms them instead of
    # re-applying. Tagging with the wrong id would double-apply the origin's steps.
    client_id = msg.get("clientID")
    if client_id is None:
        return
    client_id = str(client_id)

    async with room.lock:
        if base_version != room.version:
            # Client is behind (or ahead). Reject; it catches up from the
            # collab.steps broadcasts it is receiving and resends (its
            # dispatchTransaction re-fires sendableSteps after applying remotes).
            await websocket.send_json({"type": "collab.reject", "version": room.version})
            return
        applied_base = room.append_steps(raw_steps, client_id)
        payload = {
            "type": "collab.steps",
            "version": applied_base,
            "steps": [{"step": s, "clientID": client_id} for s in raw_steps],
        }
        # Broadcast to ALL (no exclude) — origin confirms its own steps.
        await collab_manager.broadcast(room_key, payload)


async def _handle_materialize(
    room: DraftRoom, room_key: str, draft_id: int, msg: dict[str, Any]
) -> None:
    """Persist a client's converged text to live_content (room = single writer)."""
    try:
        version = int(msg["version"])
    except (KeyError, TypeError, ValueError):
        return
    text = msg.get("text")
    if not isinstance(text, str):
        return

    new_lcv: int | None = None
    async with room.lock:
        # Version-gate: only flush a real committed version newer than the last
        # flushed one. Redundant offers from multiple peers at the same version
        # are deduped here (idempotent), so any connected client may flush.
        if not room.should_flush(version):
            return
        async with _db.SessionLocal() as session:
            new_lcv = await set_live_content(session, draft_id, text)
        if new_lcv is None:
            return  # draft deleted out from under us
        room.mark_flushed(version)

    await collab_manager.broadcast(
        room_key, {"type": "collab.flushed", "liveContentVersion": new_lcv}
    )


async def broadcast_version_rebase(draft_id: int, content: str) -> None:
    """Snap all connected editors to a newly-committed Save-version.

    Called by the HTTP PUT handler after it mints a version row and clears
    live_content. Re-hydrates the live room to the saved content at version 0
    and broadcasts ``collab.rebase`` so every editor rebuilds onto the saved
    baseline — preventing the room from re-flushing stale steps over the
    just-saved version (R12). No-op if no room is live for this draft.
    """
    room_key = str(draft_id)
    room = room_registry.peek(room_key)
    if room is None:
        return
    async with room.lock:
        room.hydrate(content)
        payload = {"type": "collab.rebase", "version": room.base_version, "doc": room.base_text}
    await collab_manager.broadcast(room_key, payload)


@router.websocket("/drafts/{draft_id}/collab")
async def collab_ws(websocket: WebSocket, draft_id: int) -> None:
    """Per-draft collab WebSocket (presence + prosemirror-collab step sync)."""
    identity = await resolve_ws_identity(websocket)
    if identity is None:
        # Close before accept() — identity is untrusted.
        await websocket.close(code=4401)
        return

    room_key = str(draft_id)
    room = room_registry.get(room_key)

    # Hydrate BEFORE registering the socket in the room. _hydrate_room awaits a
    # DB read; if the socket were already in the broadcast set, a concurrent
    # joiner's presence.join could reach it during that await and arrive BEFORE
    # its own presence.init. Hydrating first (socket not yet a broadcast target),
    # then registering and sending presence.init with no intervening await,
    # guarantees presence.init is the first frame this socket sees.
    collab_baseline = await _hydrate_room(room, draft_id)

    # connect() calls websocket.accept() and returns the new presence clientId.
    client_id = await collab_manager.connect(room_key, websocket, identity)

    try:
        # 1. presence.init to the JOINING socket only, carrying the collab baseline.
        existing_peers = collab_manager.peers(room_key, exclude=websocket)
        await websocket.send_json(
            {
                "type": "presence.init",
                "you": _peer_dict(client_id, identity.email, identity.name),
                "peers": [
                    _peer_dict(p.client_id, p.identity.email, p.identity.name)
                    for p in existing_peers
                ],
                "collab": collab_baseline,
            }
        )

        # 2. Broadcast presence.join to OTHERS (exclude the joiner).
        await collab_manager.broadcast(
            room_key,
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
                sender_client_id = collab_manager.client_id_for(room_key, websocket)
                if sender_client_id is None:
                    continue
                try:
                    from_pos = int(msg["from"])
                    to_pos = int(msg["to"])
                except (KeyError, TypeError, ValueError):
                    continue
                await collab_manager.broadcast(
                    room_key,
                    {
                        "type": "peer.selection",
                        "clientId": sender_client_id,
                        "from": from_pos,
                        "to": to_pos,
                    },
                    exclude=websocket,
                )
            elif msg_type == "steps":
                await _handle_steps(room, room_key, websocket, msg)
            elif msg_type == "materialize":
                await _handle_materialize(room, room_key, draft_id, msg)
            # Unknown type — ignore defensively.

    except WebSocketDisconnect:
        pass
    finally:
        departing_client_id = collab_manager.disconnect(room_key, websocket)
        if departing_client_id is not None:
            await collab_manager.broadcast(
                room_key,
                {"type": "presence.leave", "clientId": departing_client_id},
            )
        # Drop the step-log room once the last connection leaves (R11: in-flight
        # unflushed edits are already persisted via materialize; a returning
        # solo editor re-inits from live_content).
        if collab_manager.room_count(room_key) == 0:
            room_registry.drop(room_key)
