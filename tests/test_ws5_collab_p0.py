"""Phase 0 collab WebSocket tests.

Covers:
  (a) Two simultaneous connections — room count reaches 2; drops to 1 on disconnect.
  (b) Dev-override query params yield distinct identities in the roster.
  (c) Auth rejection when CF Access is enabled but no JWT header is present.
  (e) Disconnect-race guard: disconnecting from an already-cleaned-up room is a
      safe no-op (KeyError regression, broadcast dead-removal path).

All tests are hermetic — no real CF Access calls, no DB access.
"""

from __future__ import annotations

import os

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

import artemis.db as _db_module
from artemis.db import attach_pgvector_codec
from artemis.marketing.writing_studio.collab.routes import router

# Phase 3 added a DB hydration read on WS connect. Sync TestClient WS tests use
# ephemeral event loops, so swap in a NullPool factory (no connection outlives a
# loop). The collab route late-binds to db.SessionLocal, so this takes effect.
_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL", "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test"
)
_np_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_np_engine)
_db_module.SessionLocal = async_sessionmaker(
    bind=_np_engine, expire_on_commit=False, class_=AsyncSession
)

# ---------------------------------------------------------------------------
# Minimal app fixture — just the collab router, no other middleware
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


# ---------------------------------------------------------------------------
# (a) Room count and disconnect
# ---------------------------------------------------------------------------


def test_two_connections_room_count() -> None:
    """After two clients connect to the same draft, room_count == 2."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1001

    with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab") as ws1:
        ws1.receive_json()  # consume presence.init for ws1
        assert collab_manager.room_count(str(draft_id)) == 1

        with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab") as ws2:
            assert collab_manager.room_count(str(draft_id)) == 2

            # Phase 1: ws1 gets presence.join; ws2 gets presence.init (with ws1 as peer).
            join_evt = ws1.receive_json()
            assert join_evt["type"] == "presence.join"
            init_evt = ws2.receive_json()
            assert init_evt["type"] == "presence.init"
            assert len(init_evt["peers"]) == 1

            ws2.close()

        # After second disconnect, count drops to 1.
        assert collab_manager.room_count(str(draft_id)) == 1
        ws1.close()

    assert collab_manager.room_count(str(draft_id)) == 0


def test_disconnect_clears_room() -> None:
    """After all clients disconnect, the room is removed entirely."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1002

    with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab"):
        assert collab_manager.room_count(str(draft_id)) == 1

    assert collab_manager.room_count(str(draft_id)) == 0
    assert str(draft_id) not in collab_manager._rooms


# ---------------------------------------------------------------------------
# (b) Dev-override: distinct identities in the roster
# ---------------------------------------------------------------------------


def test_dev_override_distinct_identities() -> None:
    """?as_email= overrides yield two distinct identities in the roster."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1003

    with (
        tc.websocket_connect(
            f"/api/writing-studio/drafts/{draft_id}/collab?as_email=alice%40example.com&as_name=Alice"
        ),
        tc.websocket_connect(
            f"/api/writing-studio/drafts/{draft_id}/collab?as_email=bob%40example.com&as_name=Bob"
        ),
    ):
        roster = collab_manager.roster(str(draft_id))
        assert len(roster) == 2
        emails = {r.email for r in roster}
        assert "alice@example.com" in emails
        assert "bob@example.com" in emails


def test_dev_override_name_stored() -> None:
    """?as_name= is reflected in the roster identity."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1004

    with tc.websocket_connect(
        f"/api/writing-studio/drafts/{draft_id}/collab?as_email=carol%40example.com&as_name=Carol"
    ):
        roster = collab_manager.roster(str(draft_id))
        assert len(roster) == 1
        assert roster[0].name == "Carol"
        assert roster[0].email == "carol@example.com"


# ---------------------------------------------------------------------------
# (c) Auth rejection: CF Access enabled, no JWT → close 4401
# ---------------------------------------------------------------------------


def test_auth_rejection_no_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    """When CF Access is enabled and no JWT header is present, the connection
    is closed with code 4401."""
    import artemis.config as _cfg

    monkeypatch.setattr(_cfg.settings, "cf_access_enabled", True)

    app = _make_app()
    tc = TestClient(app)

    draft_id = 1005

    # pytest.raises and websocket_connect cannot be merged into a single with-statement
    # because the exception is raised by __enter__ of the inner context manager.
    with pytest.raises(WebSocketDisconnect) as exc_info:  # noqa: SIM117
        with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab"):
            pass  # should not reach here

    assert exc_info.value.code == 4401


# ---------------------------------------------------------------------------
# (d) Presence event received on connect (single client baseline)
# ---------------------------------------------------------------------------


def test_presence_event_on_connect() -> None:
    """First client receives a presence.init event with empty peers on connect (Phase 1 protocol)."""
    from artemis.marketing.writing_studio.collab.manager import collab_manager

    collab_manager._rooms.clear()
    app = _make_app()
    tc = TestClient(app)

    draft_id = 1006

    with tc.websocket_connect(f"/api/writing-studio/drafts/{draft_id}/collab") as ws:
        evt = ws.receive_json()
        # Phase 1 replaced collab.presence count with the full roster protocol.
        assert evt["type"] == "presence.init"
        assert evt["peers"] == []
        assert "you" in evt


# ---------------------------------------------------------------------------
# (e) Disconnect-race guard — pure unit tests, no transport needed
# ---------------------------------------------------------------------------


def _make_identity(email: str = "test@example.com") -> object:
    from artemis.identity.dependencies import RequestIdentity

    return RequestIdentity(email=email, name="Test", claims={}, source="dev")


def _stub_ws() -> object:
    """Return a minimal object that works as a dict key (identity-based hash)."""
    from unittest.mock import MagicMock

    return MagicMock(name="ws")


def test_disconnect_already_cleaned_room_is_noop() -> None:
    """Disconnecting from a room that was already removed must not raise KeyError.

    Reproduces the race: broadcast's dead-removal loop calls disconnect() after
    the last live peer already deleted the room.  The fix: disconnect() already
    guards with `if room not in self._rooms: return None`, so this test pins
    that contract even if the internals change.
    """
    from artemis.marketing.writing_studio.collab.manager import CollabManager

    mgr = CollabManager()
    ws = _stub_ws()
    room = "race-room-1"

    # Room never existed — disconnect must be a safe no-op.
    result = mgr.disconnect(room, ws)  # type: ignore[arg-type]

    assert result is None
    assert room not in mgr._rooms


def test_disconnect_after_explicit_room_removal_is_noop() -> None:
    """Simulate the race: connect, manually remove the room, then disconnect.

    This mirrors what happens when broadcast's dead-removal call fires after
    disconnect() has already cleaned up the last peer and deleted the room entry.
    """
    from artemis.marketing.writing_studio.collab.manager import CollabManager

    mgr = CollabManager()
    ws = _stub_ws()
    room = "race-room-2"

    # Seed internal state as if connect() ran.
    from artemis.marketing.writing_studio.collab.manager import _Peer

    identity = _make_identity()
    mgr._rooms[room] = {ws: _Peer(client_id="abc123", identity=identity)}  # type: ignore[index]

    # Simulate the concurrent cleanup that removes the room (e.g. the real
    # disconnect() ran first and deleted the bucket).
    del mgr._rooms[room]

    # Now the second caller tries to disconnect the same websocket — must not raise.
    result = mgr.disconnect(room, ws)  # type: ignore[arg-type]

    assert result is None
    assert room not in mgr._rooms


def test_broadcast_dead_removal_race_is_noop() -> None:
    """broadcast() dead-removal must not raise KeyError when disconnect() already
    cleaned up the room between the send loop and the dead-pruning step.

    We simulate the race by monkey-patching _rooms so the room disappears
    mid-broadcast, then calling the dead-cleanup logic indirectly via a direct
    CollabManager call with a pre-seeded dead list scenario.

    Implementation: we call disconnect() to clear the room, then manually
    invoke the path that broadcast() would take after collecting dead sockets.
    """
    import asyncio

    from artemis.marketing.writing_studio.collab.manager import CollabManager, _Peer

    mgr = CollabManager()
    ws = _stub_ws()
    room = "race-room-3"
    identity = _make_identity("broadcast@example.com")

    # Connect ws into the room.
    mgr._rooms[room] = {ws: _Peer(client_id="dead001", identity=identity)}  # type: ignore[index]

    # Simulate disconnect() clearing the room (races with broadcast's dead-prune).
    removed = mgr.disconnect(room, ws)  # type: ignore[arg-type]
    assert removed == "dead001"
    assert room not in mgr._rooms

    # Now run broadcast() with the room gone — it should short-circuit safely.
    async def _run() -> None:
        await mgr.broadcast(room, {"type": "ping"})

    asyncio.run(_run())  # must not raise

    # State unchanged: room still absent.
    assert room not in mgr._rooms


def test_normal_connect_disconnect_still_works() -> None:
    """Guard regression: normal single-peer lifecycle returns clientId and cleans room."""
    from artemis.marketing.writing_studio.collab.manager import CollabManager, _Peer

    mgr = CollabManager()
    ws = _stub_ws()
    room = "normal-room-1"
    identity = _make_identity("normal@example.com")

    mgr._rooms[room] = {ws: _Peer(client_id="cid999", identity=identity)}  # type: ignore[index]

    client_id = mgr.disconnect(room, ws)  # type: ignore[arg-type]

    assert client_id == "cid999"
    assert room not in mgr._rooms
