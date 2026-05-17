"""Floating Artemis HTTP + WebSocket routes.

HTTP endpoints:
  POST   /api/floating-artemis/sessions
  GET    /api/floating-artemis/sessions
  GET    /api/floating-artemis/sessions/{session_id}
  PATCH  /api/floating-artemis/sessions/{session_id}
  DELETE /api/floating-artemis/sessions/{session_id}
  POST   /api/floating-artemis/sessions/{session_id}/messages
  GET    /api/floating-artemis/sessions/{session_id}/messages
  POST   /api/floating-artemis/sessions/{session_id}/page-context
  POST   /api/floating-artemis/sessions/{session_id}/tool-confirm
  POST   /api/floating-artemis/sessions/{session_id}/stop

WebSocket:
  WS     /ws/floating-artemis/{session_id}
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.floating_artemis import repository as repo
from artemis.floating_artemis.authority import confirmation_store
from artemis.floating_artemis.chat import handle_turn, resume_after_confirm
from artemis.floating_artemis.memory_read_cache import get as memory_cache_get
from artemis.floating_artemis.schemas import (
    MemoryReadEvent,
    MessageRead,
    PageContextRead,
    PageContextSet,
    SessionCreate,
    SessionRead,
    SessionUpdate,
    ToolConfirmRequest,
    ToolConfirmResponse,
    TurnRequest,
)
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/floating-artemis",
    tags=["floating-artemis"],
    dependencies=[Depends(require_token)],
)

ws_router = APIRouter(tags=["floating-artemis-ws"])


# ── Session endpoints ─────────────────────────────────────────────────────────


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_session_by_id(session, body.session_id)
        raise conflict(f"Session '{body.session_id}' already exists", "session_exists")
    except ValueError:
        pass
    row = await repo.create_session(
        session,
        session_id=body.session_id,
        owner_user_id=body.owner_user_id,
        title=body.title,
        metadata=body.metadata,
    )
    await session.commit()
    return SessionRead.from_orm_row(row).model_dump()


@router.get("/sessions")
async def list_sessions(
    owner_user_id: int | None = Query(default=None),
    include_closed: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    rows = await repo.list_sessions(
        session,
        owner_user_id=owner_user_id,
        limit=limit,
        cursor=cursor,
        include_closed=include_closed,
    )
    return {"sessions": [SessionRead.from_orm_row(r).model_dump() for r in rows]}


@router.get("/sessions/{session_id}")
async def get_session_route(
    session_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        row = await repo.get_session_by_id(session, session_id)
    except ValueError:
        raise not_found(f"Session '{session_id}' not found", "session_not_found")  # noqa: B904
    return SessionRead.from_orm_row(row).model_dump()


@router.patch("/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: SessionUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    update_data = body.model_dump(exclude_none=True)
    if not update_data:
        raise bad_request("No fields to update", "empty_update")
    try:
        row = await repo.update_session(session, session_id, **update_data)
    except ValueError:
        raise not_found(f"Session '{session_id}' not found", "session_not_found")  # noqa: B904
    await session.commit()
    return SessionRead.from_orm_row(row).model_dump()


@router.delete("/sessions/{session_id}", status_code=204)
async def close_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.close_session(session, session_id)
    except ValueError:
        raise not_found(f"Session '{session_id}' not found", "session_not_found")  # noqa: B904
    await session.commit()


@router.post("/sessions/{session_id}/archive")
async def archive_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Archive the session ('Start fresh'). Marks closed + archived=true.

    Unlike DELETE, archived sessions remain queryable via include_closed=true
    and will surface in a future session-history view. Not destructive.
    """
    try:
        row = await repo.archive_session(session, session_id)
    except ValueError:
        raise not_found(f"Session '{session_id}' not found", "session_not_found")  # noqa: B904
    await session.commit()
    await ws_manager.broadcast(
        f"fa:{session_id}",
        {"type": "floating_artemis.archived", "session_id": session_id},
    )
    return SessionRead.from_orm_row(row).model_dump()


# ── Message endpoints ─────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/messages", status_code=202)
async def send_message(
    session_id: str,
    body: TurnRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Queue a user turn. Runs handle_turn as a background task; returns immediately."""
    # Verify session exists
    try:
        await repo.get_session_by_id(session, session_id)
    except ValueError:
        raise not_found(f"Session '{session_id}' not found", "session_not_found")  # noqa: B904

    # Fire and forget — client receives events over WebSocket
    asyncio.create_task(
        handle_turn(session_id=session_id, user_text=body.message),
        name=f"fa_turn_{session_id}",
    )
    return {"accepted": True, "session_id": session_id}


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    msgs = await repo.list_messages(session, session_id, limit=limit, cursor=cursor)
    return {"messages": [MessageRead.model_validate(m).model_dump() for m in msgs]}


# ── Page context ──────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/page-context", status_code=201)
async def set_page_context(
    session_id: str,
    body: PageContextSet,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_session_by_id(session, session_id)
    except ValueError:
        raise not_found(f"Session '{session_id}' not found", "session_not_found")  # noqa: B904
    ctx = await repo.set_page_context(
        session, session_id=session_id, page=body.page, ref_id=body.ref_id
    )
    await session.commit()
    return PageContextRead.model_validate(ctx).model_dump()


# ── Tool confirmation ─────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/tool-confirm")
async def tool_confirm(
    session_id: str,
    body: ToolConfirmRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ToolConfirmResponse:
    """Operator confirms or cancels a pending layer-3/4 tool call."""
    pending = confirmation_store.get(body.tool_use_id)
    if pending is None or pending.session_id != session_id:
        raise not_found(
            f"No pending confirmation for tool_use_id={body.tool_use_id!r}",
            "confirmation_not_found",
        )

    # Resume the suspended turn
    asyncio.create_task(
        resume_after_confirm(
            session_id=session_id,
            tool_use_id=body.tool_use_id,
            decision=body.decision,
        ),
        name=f"fa_confirm_{body.tool_use_id}",
    )

    return ToolConfirmResponse(
        tool_use_id=body.tool_use_id,
        decision=body.decision,
    )


# ── Stop ──────────────────────────────────────────────────────────────────────


@router.post("/sessions/{session_id}/stop")
async def stop_session_turn(
    session_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Request graceful stop for in-progress turn. Clears pending confirmations."""
    confirmation_store.clear_session(session_id)
    await ws_manager.broadcast(
        f"fa:{session_id}",
        {"type": "floating_artemis.stopped", "session_id": session_id},
    )
    return {"stopped": True, "session_id": session_id}


# ── Active runs ───────────────────────────────────────────────────────────────


@router.get("/active-runs")
async def get_active_runs(
    owner_user_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    rows = await repo.get_active_runs(session, owner_user_id=owner_user_id)
    return {"runs": rows}


# ── Memory read backfill ──────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/memory-reads/latest")
async def get_latest_memory_reads(
    session_id: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> MemoryReadEvent:
    """Return the most recent MemoryReadEvent for this session (in-memory cache).

    Returns 204 No Content if the session has not yet executed a memory query.
    """
    from fastapi import Response

    cached = memory_cache_get(session_id)
    if cached is None:
        return Response(status_code=204)  # type: ignore[return-value]
    return cached


# ── WebSocket endpoint ────────────────────────────────────────────────────────


@ws_router.websocket("/ws/floating-artemis/{session_id}")
async def floating_artemis_ws(session_id: str, websocket: WebSocket) -> None:
    """WebSocket connection for live Floating Artemis session events.

    Room key: fa:{session_id}
    Events broadcast on this room:
      floating_artemis.turn_started
      floating_artemis.message
      floating_artemis.tool_started
      floating_artemis.tool_completed
      floating_artemis.tool_pending
      floating_artemis.turn_complete
      floating_artemis.failed
      floating_artemis.stopped
    """
    room = f"fa:{session_id}"
    await ws_manager.connect(room, websocket)
    logger.debug("fa:ws:connect session_id=%s", session_id)

    # Send initial connection acknowledgement
    with contextlib.suppress(Exception):
        await websocket.send_json({"type": "floating_artemis.connected", "session_id": session_id})

    try:
        while True:
            # Keep connection alive; events are pushed from handle_turn via broadcast.
            # We process incoming ping/pong messages to detect disconnects.
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo pings back as pongs
                if data == "ping":
                    await websocket.send_text("pong")
            except TimeoutError:
                # Send keepalive
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.debug("fa:ws:disconnect session_id=%s", session_id)
    except Exception:
        logger.debug("fa:ws:error session_id=%s", session_id, exc_info=True)
    finally:
        await ws_manager.disconnect(room, websocket)
