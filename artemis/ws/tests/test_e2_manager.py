"""Tests for WebSocketManager — connect/disconnect lifecycle, broadcast, dead-connection cleanup."""

from __future__ import annotations

import pytest

from artemis.ws.manager import WebSocketManager


class _FakeWebSocket:
    """Minimal WebSocket stub for testing."""

    def __init__(self, *, accept_raises: bool = False, send_raises: bool = False) -> None:
        self._accept_raises = accept_raises
        self._send_raises = send_raises
        self.accepted = False
        self.sent: list[object] = []

    async def accept(self) -> None:
        if self._accept_raises:
            raise RuntimeError("accept failed")
        self.accepted = True

    async def send_json(self, data: object) -> None:
        if self._send_raises:
            raise RuntimeError("send failed")
        self.sent.append(data)


# ---------------------------------------------------------------------------
# connect / disconnect lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connect_accepts_websocket() -> None:
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.connect("room-1", ws)
    assert ws.accepted is True


@pytest.mark.asyncio
async def test_connect_adds_to_room() -> None:
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.connect("room-1", ws)
    assert mgr.room_count("room-1") == 1


@pytest.mark.asyncio
async def test_multiple_clients_in_same_room() -> None:
    mgr = WebSocketManager()
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    await mgr.connect("room-1", ws1)
    await mgr.connect("room-1", ws2)
    assert mgr.room_count("room-1") == 2


@pytest.mark.asyncio
async def test_disconnect_removes_from_room() -> None:
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.connect("room-1", ws)
    await mgr.disconnect("room-1", ws)
    assert mgr.room_count("room-1") == 0


@pytest.mark.asyncio
async def test_disconnect_cleans_empty_room() -> None:
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.connect("room-1", ws)
    await mgr.disconnect("room-1", ws)
    # room key should be removed entirely
    assert "room-1" not in mgr._rooms


@pytest.mark.asyncio
async def test_disconnect_unknown_room_noop() -> None:
    """Disconnecting a WS from an unknown room should not raise."""
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.disconnect("ghost-room", ws)  # should not raise


@pytest.mark.asyncio
async def test_disconnect_partial_room_survives() -> None:
    """Disconnecting one client leaves the other in the room."""
    mgr = WebSocketManager()
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    await mgr.connect("room-1", ws1)
    await mgr.connect("room-1", ws2)
    await mgr.disconnect("room-1", ws1)
    assert mgr.room_count("room-1") == 1


# ---------------------------------------------------------------------------
# broadcast
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_delivers_to_all_in_room() -> None:
    mgr = WebSocketManager()
    ws1 = _FakeWebSocket()
    ws2 = _FakeWebSocket()
    await mgr.connect("room-1", ws1)
    await mgr.connect("room-1", ws2)
    await mgr.broadcast("room-1", {"type": "ping"})
    assert ws1.sent == [{"type": "ping"}]
    assert ws2.sent == [{"type": "ping"}]


@pytest.mark.asyncio
async def test_broadcast_unknown_room_noop() -> None:
    """Broadcasting to an empty / unknown room should not raise."""
    mgr = WebSocketManager()
    await mgr.broadcast("ghost-room", {"type": "ping"})  # must not raise


@pytest.mark.asyncio
async def test_broadcast_multiple_events() -> None:
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.connect("room-1", ws)
    await mgr.broadcast("room-1", {"type": "a"})
    await mgr.broadcast("room-1", {"type": "b"})
    assert ws.sent == [{"type": "a"}, {"type": "b"}]


# ---------------------------------------------------------------------------
# dead-connection cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_connection_removed_on_broadcast() -> None:
    """A WebSocket that raises on send_json is silently removed from the room."""
    mgr = WebSocketManager()
    good = _FakeWebSocket()
    dead = _FakeWebSocket(send_raises=True)
    await mgr.connect("room-1", good)
    await mgr.connect("room-1", dead)
    # broadcast should not raise even though `dead` fails
    await mgr.broadcast("room-1", {"type": "ping"})
    # good connection should have received the event
    assert good.sent == [{"type": "ping"}]
    # dead connection should have been removed
    assert dead not in mgr._rooms.get("room-1", set())


@pytest.mark.asyncio
async def test_all_dead_connections_cleans_room() -> None:
    """If every connection in a room fails, the room is removed."""
    mgr = WebSocketManager()
    dead1 = _FakeWebSocket(send_raises=True)
    dead2 = _FakeWebSocket(send_raises=True)
    await mgr.connect("room-1", dead1)
    await mgr.connect("room-1", dead2)
    await mgr.broadcast("room-1", {"type": "ping"})
    assert "room-1" not in mgr._rooms


# ---------------------------------------------------------------------------
# room_count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_room_count_zero_for_unknown_room() -> None:
    mgr = WebSocketManager()
    assert mgr.room_count("ghost") == 0


@pytest.mark.asyncio
async def test_room_count_tracks_connects() -> None:
    mgr = WebSocketManager()
    ws = _FakeWebSocket()
    await mgr.connect("r", ws)
    assert mgr.room_count("r") == 1
