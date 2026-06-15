"""Dev Projects HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.dev_projects import repository as repo
from artemis.dev_projects.loop_runner import broadcast, decide_permission, run_turn
from artemis.dev_projects.schemas import (
    DevAnnotationCreate,
    DevAnnotationRead,
    DevForkCreate,
    DevMessageCreate,
    DevMessageRead,
    DevProjectCreate,
    DevProjectRead,
    DevProjectUpdate,
    DevSessionCreate,
    DevSessionDetail,
    DevSessionRead,
    DevSessionUpdate,
    PermissionDecision,
)
from artemis.dev_projects.service import list_project_files
from artemis.config import settings
from artemis.identity.dependencies import resolve_request_identity
from artemis.identity.scope_policy import OWNER_EMAIL
from artemis.marketing.routes._auth import require_owner, require_token
from artemis.marketing.routes._errors import bad_request, not_found
from artemis.providers import list_providers
from artemis.ws.manager import ws_manager

router = APIRouter(
    prefix="/api/dev-projects",
    tags=["dev-projects"],
    # require_token gates unauthenticated; require_owner gates non-owner.
    dependencies=[Depends(require_token), Depends(require_owner)],
)

ws_router = APIRouter(tags=["dev-projects-ws"])


def _project_read(row: Any) -> dict[str, Any]:
    return DevProjectRead(
        id=row.id,
        name=row.name,
        path=row.path,
        last_opened_at=row.last_opened_at,
        archived_at=row.archived_at,
        metadata=row.metadata_ or {},
    ).model_dump()


def _session_read(row: Any, message_count: int = 0) -> dict[str, Any]:
    return DevSessionRead(
        id=row.id,
        project_id=row.project_id,
        title=row.title,
        provider=row.provider,
        model=row.model,
        bypass_permissions=row.bypass_permissions,
        pinned=row.pinned,
        notes=row.notes or [],
        started_at=row.started_at,
        last_active_at=row.last_active_at,
        archived_at=row.archived_at,
        fork_of=row.fork_of,
        fork_at_message=row.fork_at_message,
        message_count=message_count,
    ).model_dump()


@router.get("/projects")
async def list_projects(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:  # noqa: B008
    rows = await repo.list_projects(session)
    return {"projects": [_project_read(row) for row in rows]}


@router.get("/browse")
async def browse_directories(path: str = Query(default="~")) -> dict[str, Any]:
    """List local subdirectories for the project-folder picker.

    (codex) Local-only directory browsing is intentionally backend-driven
    because browser-native directory handles cannot expose absolute paths.
    """
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": f"Path does not exist: {path}", "code": "path_not_found"},
        ) from exc

    if not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"error": f"Not a directory: {resolved}", "code": "not_a_directory"},
        )

    entries = []
    try:
        for entry in sorted(resolved.iterdir(), key=lambda item: item.name.casefold()):
            if entry.name.startswith(".") and not resolved.name.startswith("."):
                continue
            try:
                if not entry.is_dir():
                    continue
                entries.append(
                    {
                        "name": entry.name,
                        "path": str(entry.resolve()),
                        "is_git_repo": (entry / ".git").exists(),
                    }
                )
            except OSError:
                continue
    except PermissionError:
        entries = []

    parent = str(resolved.parent) if resolved.parent != resolved else None
    return {"resolved_path": str(resolved), "parent_path": parent, "entries": entries}


@router.get("/projects/browse")
async def browse_project_folders(dir: str | None = Query(default=None)) -> dict[str, Any]:
    """Compatibility wrapper for older in-app folder browser callers."""
    data = await browse_directories(dir or "~")
    return {
        "current": data["resolved_path"],
        "parent": data["parent_path"],
        "dirs": data["entries"],
    }


@router.post("/projects/validate-path")
async def validate_project_path(body: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(body.get("path") or "").strip()
    if not raw_path:
        return {"ok": False, "exists": False, "is_dir": False, "error": "Path is required"}
    path = Path(raw_path).expanduser()
    exists = path.exists()
    is_dir = path.is_dir()
    return {
        "ok": exists and is_dir,
        "exists": exists,
        "is_dir": is_dir,
        "path": str(path.resolve()) if exists else str(path),
        "error": None
        if exists and is_dir
        else ("Path does not exist" if not exists else "Not a directory"),
    }


@router.post("/projects", status_code=201)
async def create_project(
    body: DevProjectCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    row = await repo.create_project(session, name=body.name, path=body.path)
    await session.commit()
    return _project_read(row)


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: int,
    body: DevProjectUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        row = await repo.update_project(
            session,
            project_id,
            name=body.name,
            archived=body.archived,
            metadata=body.metadata,
        )
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    await session.commit()
    return _project_read(row)


@router.delete("/projects/{project_id}", status_code=204)
async def archive_project(
    project_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.archive_project(session, project_id)
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    await session.commit()


@router.delete("/projects/{project_id}/permanent", status_code=204)
async def delete_project_permanently(
    project_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_project(session, project_id)
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    await session.commit()


@router.post("/projects/{project_id}/open")
async def open_project_folder(
    project_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        project = await repo.get_project(session, project_id)
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    path = Path(project.path)
    if not path.is_dir():
        raise bad_request("Project folder does not exist", "project_path_missing")
    opener = "open" if os.uname().sysname == "Darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(path)}


@router.get("/projects/{project_id}/sessions")
async def list_project_sessions(
    project_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        rows = await repo.list_sessions(session, project_id)
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    return {"sessions": [_session_read(row, count) for row, count in rows]}


@router.post("/projects/{project_id}/sessions", status_code=201)
async def create_project_session(
    project_id: int,
    body: DevSessionCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        project = await repo.get_project(session, project_id)
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    defaults = project.metadata_ or {}
    provider = body.provider or str(defaults.get("default_provider") or "claude-code")
    model = body.model if body.model is not None else defaults.get("default_model")
    if provider not in list_providers():
        raise bad_request(f"Unknown provider {provider!r}", "unknown_provider")
    try:
        row = await repo.create_session(
            session,
            project_id=project_id,
            provider=provider,
            model=str(model) if model else None,
            title=body.title,
        )
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    await session.commit()
    return _session_read(row)


@router.get("/sessions/{session_id}")
async def get_session_detail(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        row = await repo.get_session(session, session_id)
        messages = await repo.list_messages(session, session_id, limit=50)
        annotations = await repo.list_annotations(session, session_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    detail = DevSessionDetail(
        session=DevSessionRead(**_session_read(row, len(messages))),
        messages=[DevMessageRead.model_validate(m) for m in messages],
        annotations=[DevAnnotationRead.model_validate(a) for a in annotations],
    )
    return detail.model_dump()


@router.patch("/sessions/{session_id}")
async def update_project_session(
    session_id: int,
    body: DevSessionUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    if body.provider is not None and body.provider not in list_providers():
        raise bad_request(f"Unknown provider {body.provider!r}", "unknown_provider")
    try:
        row = await repo.update_session(
            session,
            session_id,
            title=body.title,
            provider=body.provider,
            model=body.model,
            bypass_permissions=body.bypass_permissions,
            pinned=body.pinned,
            archived=body.archived,
        )
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    await session.commit()
    await broadcast(
        session_id, {"type": "dev_projects.session_updated", "session": _session_read(row)}
    )
    return _session_read(row)


@router.delete("/sessions/{session_id}", status_code=204)
async def archive_project_session(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.archive_session(session, session_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    await session.commit()
    await broadcast(session_id, {"type": "dev_projects.session_archived", "session_id": session_id})


@router.delete("/sessions/{session_id}/permanent", status_code=204)
async def delete_project_session_permanently(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_session(session, session_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    await session.commit()
    await broadcast(session_id, {"type": "dev_projects.session_deleted", "session_id": session_id})


@router.post("/sessions/{session_id}/fork", status_code=201)
async def fork_session(
    session_id: int,
    body: DevForkCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        row = await repo.fork_session(
            session, source_session_id=session_id, at_message_id=body.at_message_id
        )
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    await session.commit()
    return _session_read(row)


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: int,
    cursor: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        messages = await repo.list_messages(session, session_id, cursor=cursor, limit=limit)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    return {"messages": [DevMessageRead.model_validate(m).model_dump() for m in messages]}


@router.post("/sessions/{session_id}/messages", status_code=202)
async def send_message(
    session_id: int,
    body: DevMessageCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        await repo.get_session(session, session_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    asyncio.create_task(
        run_turn(session_id, body.text, body.images),
        name=f"dev_projects_turn_{session_id}",
    )
    return {"accepted": True, "session_id": session_id}


@router.post("/sessions/{session_id}/permissions/{permission_id}/approve")
async def approve_permission(
    session_id: int,
    permission_id: str,
    body: PermissionDecision | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    if body and body.trust_for_session:
        try:
            await repo.update_session(session, session_id, bypass_permissions=True)
            await session.commit()
        except ValueError:
            raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    if not await decide_permission(
        session_id=session_id, permission_id=permission_id, approved=True
    ):
        raise not_found("Permission request not found", "permission_not_found")
    return {"ok": True, "decision": "approve"}


@router.post("/sessions/{session_id}/permissions/{permission_id}/deny")
async def deny_permission(session_id: int, permission_id: str) -> dict[str, Any]:
    if not await decide_permission(
        session_id=session_id, permission_id=permission_id, approved=False
    ):
        raise not_found("Permission request not found", "permission_not_found")
    return {"ok": True, "decision": "deny"}


@router.get("/sessions/{session_id}/annotations")
async def list_annotations(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        rows = await repo.list_annotations(session, session_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    return {"annotations": [DevAnnotationRead.model_validate(r).model_dump() for r in rows]}


@router.post("/sessions/{session_id}/annotations", status_code=201)
async def create_annotation(
    session_id: int,
    body: DevAnnotationCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        row = await repo.add_annotation(
            session, session_id=session_id, url=body.url, note=body.note
        )
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904
    await session.commit()
    payload = DevAnnotationRead.model_validate(row).model_dump()
    await broadcast(session_id, {"type": "dev_projects.annotation", "annotation": payload})
    return payload


@router.delete("/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    try:
        await repo.delete_annotation(session, annotation_id)
    except ValueError:
        raise not_found(f"Annotation {annotation_id} not found", "annotation_not_found")  # noqa: B904
    await session.commit()


@router.get("/projects/{project_id}/files")
async def search_project_files(
    project_id: int,
    q: str = Query(default=""),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    try:
        project = await repo.get_project(session, project_id)
    except ValueError:
        raise not_found(f"Project {project_id} not found", "project_not_found")  # noqa: B904
    return {"files": [item.model_dump() for item in list_project_files(project.path, q)]}


@ws_router.websocket("/ws/dev-projects/{session_id}")
async def dev_projects_ws(session_id: int, websocket: WebSocket) -> None:
    """Owner-only WebSocket.

    HTTP dependencies cannot be injected into WebSocket handlers, so we gate
    inline.  In dev mode (CF Access disabled) the sole local user is the owner.
    With CF Access enabled, the verified identity email must be OWNER_EMAIL.
    Non-owner or unresolved identity → close immediately with code 4403.
    """
    if settings.cf_access_enabled:
        try:
            identity = await resolve_request_identity(websocket)  # type: ignore[arg-type]
            if identity.email.strip().lower() != OWNER_EMAIL:
                await websocket.close(code=4403)
                return
        except Exception:
            await websocket.close(code=4403)
            return

    room = f"dev-projects:{session_id}"
    await ws_manager.connect(room, websocket)
    try:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json({"type": "dev_projects.connected", "session_id": session_id})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(room, websocket)
