"""Tests for WS auth (ARTEMIS_TOKEN)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from artemis.ws.routes import router


@pytest.fixture()
def app() -> FastAPI:
    a = FastAPI()
    a.include_router(router)
    return a


# ---------------------------------------------------------------------------
# When ARTEMIS_TOKEN is not set — all connections allowed
# ---------------------------------------------------------------------------


def test_no_auth_configured_allows_all(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARTEMIS_TOKEN", raising=False)
    tc = TestClient(app)
    with tc.websocket_connect("/ws/agent-runs/run-1") as ws:
        ws.close()


# ---------------------------------------------------------------------------
# When ARTEMIS_TOKEN is set
# ---------------------------------------------------------------------------


def test_correct_token_in_query_param_accepted(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    tc = TestClient(app)
    with tc.websocket_connect("/ws/agent-runs/run-1?token=secret-token") as ws:
        ws.close()


def test_missing_token_rejected(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    tc = TestClient(app, raise_server_exceptions=False)
    # The server closes with 1008; Starlette TestClient raises WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect), tc.websocket_connect("/ws/agent-runs/run-1") as ws:
        ws.receive_json()


def test_wrong_token_rejected(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    tc = TestClient(app, raise_server_exceptions=False)
    with (
        pytest.raises(WebSocketDisconnect),
        tc.websocket_connect("/ws/agent-runs/run-1?token=wrong-token") as ws,
    ):
        ws.receive_json()


def test_correct_token_in_header_accepted(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTEMIS_TOKEN", "secret-token")
    tc = TestClient(app)
    with tc.websocket_connect(
        "/ws/agent-runs/run-1",
        headers={"Sec-WebSocket-Protocol": "secret-token"},
    ) as ws:
        ws.close()
