"""FastAPI WebSocket endpoints for live agent/workflow run streaming.

Auth: if ``ARTEMIS_TOKEN`` env var is set, clients must supply the same
value via the ``?token=`` query param or ``Sec-WebSocket-Protocol`` header.
Mismatches close with code 1008 (policy violation).

Initial state: on connect, the current run status and any stored context
entries are pushed to the new subscriber before ongoing broadcasts arrive.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

_ARTEMIS_TOKEN_ENV = "ARTEMIS_TOKEN"


def _get_required_token() -> str | None:
    """Return the required token, or None if auth is disabled."""
    return os.environ.get(_ARTEMIS_TOKEN_ENV) or None


def _check_auth(websocket: WebSocket, required: str) -> bool:
    """Return True if the client supplied the correct token."""
    # Try query param first
    token_param = websocket.query_params.get("token")
    if token_param == required:
        return True
    # Try Sec-WebSocket-Protocol header (some clients use it to pass auth)
    proto_header = websocket.headers.get("Sec-WebSocket-Protocol", "")
    return proto_header == required


async def _reject(websocket: WebSocket, reason: str) -> None:
    """Accept then immediately close with 1008 (policy violation)."""
    await websocket.accept()
    await websocket.close(code=1008, reason=reason)


async def _send_initial_agent_state(websocket: WebSocket, run_id: str) -> None:
    """Push the current run state to a freshly connected subscriber.

    This is best-effort: if the run_id is unknown (not yet committed, or
    cleaned up) we skip gracefully. A DB session is opened and closed here
    so we don't hold a connection for the lifetime of the WS.
    """
    try:
        import artemis.db as _db

        async with _db.SessionLocal() as session:
            from artemis.builders.repository import get_agent_run

            try:
                run = await get_agent_run(session, run_id)
            except ValueError:
                # Run not found yet — the client joined before the DB row was
                # flushed. Send nothing; the executor will broadcast events.
                return

            await websocket.send_json(
                {
                    "type": "agent_run.state",
                    "run_id": run_id,
                    "payload": {
                        "status": run.status,
                        "agent_id": run.agent_id,
                        "user_message": run.user_message,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                        "completed_at": (
                            run.completed_at.isoformat() if run.completed_at else None
                        ),
                        "error": run.error,
                    },
                }
            )
    except Exception:
        logger.debug("ws:initial_state failed for run_id=%s — continuing", run_id, exc_info=True)


async def _send_initial_workflow_state(websocket: WebSocket, run_id: str) -> None:
    """Push the current workflow run state to a freshly connected subscriber."""
    try:
        import artemis.db as _db

        async with _db.SessionLocal() as session:
            from artemis.builders.repository import get_workflow_run

            try:
                run = await get_workflow_run(session, run_id)
            except ValueError:
                return

            await websocket.send_json(
                {
                    "type": "workflow_run.state",
                    "run_id": run_id,
                    "payload": {
                        "status": run.status,
                        "workflow_id": run.workflow_id,
                        "current_step": run.current_step,
                        "started_at": run.started_at.isoformat() if run.started_at else None,
                        "completed_at": (
                            run.completed_at.isoformat() if run.completed_at else None
                        ),
                        "total_cost_usd": run.total_cost_usd,
                    },
                }
            )
    except Exception:
        logger.debug("ws:initial_wf_state failed for run_id=%s — continuing", run_id, exc_info=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.websocket("/ws/agent-runs/{run_id}")
async def agent_run_stream(websocket: WebSocket, run_id: str) -> None:
    """Stream live events for an agent run.

    Clients connect and receive:
    1. An ``agent_run.state`` snapshot (current DB state).
    2. Ongoing ``agent_run.*`` broadcast events as the run progresses.
    """
    required = _get_required_token()
    if required and not _check_auth(websocket, required):
        await _reject(websocket, "policy violation: missing or invalid token")
        return

    await ws_manager.connect(run_id, websocket)
    try:
        # Push current state so the client isn't waiting for the next event.
        if websocket.client_state == WebSocketState.CONNECTED:
            await _send_initial_agent_state(websocket, run_id)

        # Keep the connection open; clients receive broadcasts from executors.
        while True:
            # receive_text blocks until a frame arrives or the connection drops.
            # We ignore the payload — this acts as a keep-alive / heartbeat.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(run_id, websocket)


@router.websocket("/ws/workflow-runs/{run_id}")
async def workflow_run_stream(websocket: WebSocket, run_id: str) -> None:
    """Stream live events for a workflow run."""
    required = _get_required_token()
    if required and not _check_auth(websocket, required):
        await _reject(websocket, "policy violation: missing or invalid token")
        return

    await ws_manager.connect(run_id, websocket)
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await _send_initial_workflow_state(websocket, run_id)

        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(run_id, websocket)
