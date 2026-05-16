"""Tests for the WS endpoint routes (/ws/agent-runs/{run_id} and /ws/workflow-runs/{run_id}).

Uses Starlette's synchronous TestClient (which supports websocket_connect) so
we don't need a running server. We build a minimal FastAPI app — just the ws
router — to keep tests fast and isolated.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from starlette.testclient import TestClient

from artemis.ws.manager import WebSocketManager
from artemis.ws.routes import router

# ---------------------------------------------------------------------------
# Helper: minimal app using just the ws router
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def app() -> FastAPI:
    return _make_app()


@pytest.fixture()
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Basic connectivity
# ---------------------------------------------------------------------------


def test_agent_run_ws_connects(client: TestClient) -> None:
    """Client can connect to agent-run stream and close cleanly."""
    with client.websocket_connect("/ws/agent-runs/run-test-1") as ws:
        ws.close()


def test_workflow_run_ws_connects(client: TestClient) -> None:
    """Client can connect to workflow-run stream and close cleanly."""
    with client.websocket_connect("/ws/workflow-runs/wf-run-1") as ws:
        ws.close()


# ---------------------------------------------------------------------------
# Room tracking via an auxiliary test app
# ---------------------------------------------------------------------------

# We need a separate minimal endpoint to test room tracking without coupling
# to the actual DB-backed initial-state logic. Build a standalone test endpoint
# that uses a fresh WebSocketManager instance.

_room_mgr = WebSocketManager()


async def _room_endpoint(websocket: WebSocket, run_id: str) -> None:
    await _room_mgr.connect(run_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _room_mgr.disconnect(run_id, websocket)


_room_app = FastAPI()
_room_app.websocket("/ws/rooms/{run_id}")(_room_endpoint)


@pytest.fixture(autouse=True)
def reset_room_mgr() -> None:
    """Clear the room manager state between tests."""
    _room_mgr._rooms.clear()


def test_connection_adds_to_room() -> None:
    """Connecting should add the WS to the manager's room."""
    tc = TestClient(_room_app)
    with tc.websocket_connect("/ws/rooms/my-run"):
        assert _room_mgr.room_count("my-run") == 1


def test_disconnect_clears_room() -> None:
    """After disconnect the room should be empty."""
    tc = TestClient(_room_app)
    with tc.websocket_connect("/ws/rooms/my-run"):
        pass  # disconnect happens on __exit__
    assert _room_mgr.room_count("my-run") == 0


# ---------------------------------------------------------------------------
# Broadcast received after publish
# ---------------------------------------------------------------------------

_bcast_mgr = WebSocketManager()


async def _bcast_endpoint(websocket: WebSocket, run_id: str) -> None:
    await _bcast_mgr.connect(run_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await _bcast_mgr.disconnect(run_id, websocket)


_bcast_app = FastAPI()
_bcast_app.websocket("/ws/bcast/{run_id}")(_bcast_endpoint)


@pytest.fixture(autouse=True)
def reset_bcast_mgr() -> None:
    _bcast_mgr._rooms.clear()


def test_broadcast_received_by_connected_client() -> None:
    """A message broadcast to a room is received by the connected WS client."""
    run_id = "broadcast-test"
    tc = TestClient(_bcast_app)
    received: list[object] = []

    with tc.websocket_connect(f"/ws/bcast/{run_id}") as ws:

        def _broadcast() -> None:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(
                _bcast_mgr.broadcast(run_id, {"type": "ping", "run_id": run_id})
            )
            loop.close()

        t = threading.Thread(target=_broadcast)
        t.start()
        t.join(timeout=2)

        data = ws.receive_json()
        received.append(data)
        ws.close()

    assert received == [{"type": "ping", "run_id": run_id}]
