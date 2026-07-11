"""FastAPI WebSocket endpoints for live agent/workflow run streaming.

Auth (evaluated on connect, in order):

1. When Cloudflare Access is enabled (``settings.cf_access_enabled``), the
   upgrade request MUST carry a valid ``Cf-Access-Jwt-Assertion`` header —
   Cloudflare injects it at the edge on every proxied request, including
   WebSocket upgrades. It is verified with the same verifier as HTTP routes
   (:mod:`artemis.identity.cf_access`). Missing/invalid JWTs close with 1008.
2. Otherwise, if the ``ARTEMIS_TOKEN`` env var is set, clients must supply the
   same value via the ``?token=`` query param or ``Sec-WebSocket-Protocol``
   header (compared constant-time). Mismatches close with 1008.
3. Otherwise (local dev: CF disabled, no token) connections are allowed.

Initial state: on connect, the current run status and any stored context
entries are pushed to the new subscriber before ongoing broadcasts arrive.
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState

from artemis.config import settings
from artemis.identity.cf_access import (
    CfAccessConfigurationError,
    CfAccessVerificationError,
    get_cf_access_verifier,
)
from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter()

_ARTEMIS_TOKEN_ENV = "ARTEMIS_TOKEN"
_CF_ACCESS_HEADER = "Cf-Access-Jwt-Assertion"


def _get_required_token() -> str | None:
    """Return the required token, or None if shared-token auth is disabled."""
    return os.environ.get(_ARTEMIS_TOKEN_ENV) or None


def _check_auth(websocket: WebSocket, required: str) -> bool:
    """Return True if the client supplied the correct shared token.

    Both candidate locations are compared with ``hmac.compare_digest`` so the
    check is constant-time (no early-exit timing oracle on the token value).
    """
    required_bytes = required.encode()
    token_param = (websocket.query_params.get("token") or "").encode()
    proto_header = (websocket.headers.get("Sec-WebSocket-Protocol") or "").encode()
    param_ok = hmac.compare_digest(token_param, required_bytes)
    header_ok = hmac.compare_digest(proto_header, required_bytes)
    return param_ok or header_ok


async def _authorize_ws(websocket: WebSocket) -> tuple[bool, str]:
    """Decide whether a WebSocket connect is allowed.

    Returns ``(allowed, reject_reason)``. When Cloudflare Access is enabled a
    valid CF JWT is REQUIRED — the shared token is not accepted as a fallback,
    so a stripped/forged header can never downgrade auth. When CF is disabled,
    the shared-token path applies if ``ARTEMIS_TOKEN`` is set; with neither
    configured (local dev) the connection is allowed.
    """
    if settings.cf_access_enabled:
        assertion = websocket.headers.get(_CF_ACCESS_HEADER)
        if not assertion:
            return False, "policy violation: Cf-Access-Jwt-Assertion required"
        try:
            await get_cf_access_verifier().verify_jwt(assertion)
        except (CfAccessConfigurationError, CfAccessVerificationError):
            return False, "policy violation: invalid Cloudflare Access token"
        return True, ""

    required = _get_required_token()
    if required is None:
        # Local dev: CF disabled and no shared token configured.
        return True, ""
    if _check_auth(websocket, required):
        return True, ""
    return False, "policy violation: missing or invalid token"


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
    allowed, reason = await _authorize_ws(websocket)
    if not allowed:
        await _reject(websocket, reason)
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
    allowed, reason = await _authorize_ws(websocket)
    if not allowed:
        await _reject(websocket, reason)
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
