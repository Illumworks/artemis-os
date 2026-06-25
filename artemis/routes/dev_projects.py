"""Dev Projects HTTP + WebSocket routes."""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.websockets import WebSocketState
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import get_session
from artemis.dev_projects import repository as repo
from artemis.dev_projects.forge_runs import get_active_run_for_session, get_run_log
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


# ── Forge active-run response models ─────────────────────────────────────────


class ForgeRunLogEntry(BaseModel):
    seq: int
    kind: str
    payload: dict[str, Any]


class ForgeRunSummary(BaseModel):
    run_id: str
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None
    log: list[ForgeRunLogEntry]


class ActiveRunResponse(BaseModel):
    active_run: ForgeRunSummary | None


# ─────────────────────────────────────────────────────────────────────────────


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
        forge_mode=row.forge_mode,
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
            forge_mode=body.forge_mode,
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


@router.get("/sessions/{session_id}/active-run")
async def get_active_run(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the in-flight ForgeRun for a session, or active_run: null.

    Called by a reconnecting device to catch up on streamed build output before
    joining the live WebSocket.  Owner-gated via the router-level dependency.
    """
    run = await get_active_run_for_session(session, session_id)
    if run is None:
        return ActiveRunResponse(active_run=None).model_dump()
    log_entries = await get_run_log(session, run.run_id)
    summary = ForgeRunSummary(
        run_id=run.run_id,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error=run.error,
        log=[
            ForgeRunLogEntry(seq=entry.seq, kind=entry.kind, payload=entry.payload or {})
            for entry in log_entries
        ],
    )
    return ActiveRunResponse(active_run=summary).model_dump()


@router.patch("/sessions/{session_id}")
async def update_project_session(
    session_id: int,
    body: DevSessionUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    if body.provider is not None and body.provider not in list_providers():
        raise bad_request(f"Unknown provider {body.provider!r}", "unknown_provider")
    update_kwargs: dict[str, Any] = {
        "title": body.title,
        "provider": body.provider,
        "model": body.model,
        "bypass_permissions": body.bypass_permissions,
        "pinned": body.pinned,
        "archived": body.archived,
    }
    if "forge_mode" in body.model_fields_set:
        update_kwargs["forge_mode"] = body.forge_mode
    try:
        row = await repo.update_session(session, session_id, **update_kwargs)
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


# ── Worktree review/merge endpoints (Forge Phase 3, chunk 3.5) ───────────────
#
# All four endpoints are owner-gated by the router-level dependency.
# They resolve the project path via the DB, and run git via _run_git below.


async def _run_git(
    *args: str,
    cwd: str | None = None,
    timeout: float = 30.0,
) -> tuple[int, str, str]:
    """Minimal async git runner for route-level git calls.

    No shell.  All args are pre-validated (project_path is a checked git repo,
    session_id is always int).  Returns (returncode, stdout, stderr).
    Never raises -- callers inspect rc.
    """
    import contextlib

    cmd = ["git"]
    if cwd is not None:
        cmd += ["-C", cwd]
    cmd += list(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        raw_out, raw_err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return -1, "", f"git {' '.join(args[:3])} timed out after {timeout}s"
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, raw_out.decode(errors="replace").strip(), raw_err.decode(errors="replace").strip()


async def _detect_base_branch(project_path: str) -> str:
    """Return the base branch name for a project git repo.

    Detection order:
    1. ``git symbolic-ref --short refs/remotes/origin/HEAD``  (e.g. ``origin/main`` -> ``main``)
    2. ``git rev-parse --abbrev-ref HEAD`` on the main tree (current checkout)
    3. Hard fallback: ``"main"``
    """
    rc, out, _ = await _run_git(
        "symbolic-ref", "--short", "refs/remotes/origin/HEAD",
        cwd=project_path,
    )
    if rc == 0 and out.strip():
        return out.strip().removeprefix("origin/")

    rc2, out2, _ = await _run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=project_path)
    if rc2 == 0 and out2.strip() and out2.strip() != "HEAD":
        return out2.strip()

    return "main"


# ── Response models ───────────────────────────────────────────────────────────


class WorktreeCommitEntry(BaseModel):
    sha: str
    subject: str


class WorktreeStatusResponse(BaseModel):
    exists: bool
    branch: str
    ahead: int
    dirty_files: int
    commits: list[WorktreeCommitEntry]


class WorktreeDiffResponse(BaseModel):
    diff: str
    truncated: bool
    branch: str
    base: str


class WorktreeMergeRequest(BaseModel):
    squash: bool = False
    message: str | None = None


class WorktreeMergeResponse(BaseModel):
    merged: bool
    into: str
    branch: str


class WorktreeDiscardResponse(BaseModel):
    discarded: bool


_DIFF_MAX_CHARS = 50_000


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/worktree/status")
async def get_worktree_status(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return status of the Forge worktree for this session.

    Always returns 200.  When the worktree directory does not exist, returns
    exists=false with all numeric fields zero and commits=[].
    """
    from artemis.dev_projects.worktree import worktree_path_for

    try:
        dev_session = await repo.get_session(session, session_id)
        project = await repo.get_project(session, dev_session.project_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904

    project_path = project.path
    branch = f"forge/session-{session_id}"
    wt_path = worktree_path_for(project_path, session_id)

    if not wt_path.exists():
        return WorktreeStatusResponse(
            exists=False,
            branch=branch,
            ahead=0,
            dirty_files=0,
            commits=[],
        ).model_dump()

    base = await _detect_base_branch(project_path)

    # Commits introduced on branch since it diverged from base.
    rc, log_out, _ = await _run_git(
        "log", "--oneline", f"{base}..{branch}",
        cwd=project_path,
    )
    commits: list[WorktreeCommitEntry] = []
    if rc == 0 and log_out.strip():
        for line in log_out.splitlines():
            parts = line.strip().split(" ", 1)
            commits.append(
                WorktreeCommitEntry(
                    sha=parts[0],
                    subject=parts[1] if len(parts) > 1 else "",
                )
            )

    # Uncommitted changes sitting in the worktree checkout.
    rc2, status_out, _ = await _run_git("status", "--porcelain", cwd=str(wt_path))
    dirty_files = (
        len([ln for ln in status_out.splitlines() if ln.strip()]) if rc2 == 0 else 0
    )

    return WorktreeStatusResponse(
        exists=True,
        branch=branch,
        ahead=len(commits),
        dirty_files=dirty_files,
        commits=commits,
    ).model_dump()


@router.get("/sessions/{session_id}/worktree/diff")
async def get_worktree_diff(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return the three-dot diff between base and the Forge branch.

    Three-dot diff (``base...branch``) shows changes introduced on the branch
    since it diverged from base, ignoring any new commits on base itself.

    Capped at 50 000 chars; ``truncated=true`` when the cap is hit.
    Returns an empty diff (200) when worktree or branch is missing.
    """
    from artemis.dev_projects.worktree import worktree_path_for

    try:
        dev_session = await repo.get_session(session, session_id)
        project = await repo.get_project(session, dev_session.project_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904

    project_path = project.path
    branch = f"forge/session-{session_id}"
    wt_path = worktree_path_for(project_path, session_id)
    base = await _detect_base_branch(project_path)

    if not wt_path.exists():
        return WorktreeDiffResponse(
            diff="", truncated=False, branch=branch, base=base
        ).model_dump()

    rc, diff_out, _ = await _run_git(
        "diff", f"{base}...{branch}",
        cwd=project_path,
        timeout=60.0,
    )
    if rc != 0:
        return WorktreeDiffResponse(
            diff="", truncated=False, branch=branch, base=base
        ).model_dump()

    truncated = len(diff_out) > _DIFF_MAX_CHARS
    return WorktreeDiffResponse(
        diff=diff_out[:_DIFF_MAX_CHARS] if truncated else diff_out,
        truncated=truncated,
        branch=branch,
        base=base,
    ).model_dump()


@router.post("/sessions/{session_id}/worktree/merge")
async def merge_worktree(
    session_id: int,
    body: WorktreeMergeRequest | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Merge the Forge branch into the project's base branch (human gate).

    Safety guards
    -------------
    - 409 when the main working tree has uncommitted changes.
    - On conflict: aborts the merge and returns 409; worktree is preserved so
      Jon can inspect / resolve.
    - Worktree + branch are deleted only after a successful merge.
    - Never uses --force.

    Squash merge
    ------------
    When ``squash=true``, ``git merge --squash`` is used followed by an explicit
    ``git commit``.  The commit message defaults to
    ``"Squash merge forge/session-{id} into {base}"``.
    """
    import logging

    from artemis.dev_projects.worktree import WorktreeError, remove_worktree

    merge_req = body if body is not None else WorktreeMergeRequest()

    try:
        dev_session = await repo.get_session(session, session_id)
        project = await repo.get_project(session, dev_session.project_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904

    project_path = project.path
    branch = f"forge/session-{session_id}"
    base = await _detect_base_branch(project_path)

    # Guard: main tree must be clean before we touch it.
    rc_st, status_out, _ = await _run_git("status", "--porcelain", cwd=project_path)
    if rc_st == 0 and status_out.strip():
        raise HTTPException(
            status_code=409,
            detail={
                "merged": False,
                "conflict": False,
                "detail": "main tree dirty, commit or stash first",
            },
        )

    # Guard: branch must exist.
    rc_br, branch_list, _ = await _run_git("branch", "--list", branch, cwd=project_path)
    if rc_br != 0 or not branch_list.strip():
        raise HTTPException(
            status_code=409,
            detail={
                "merged": False,
                "conflict": False,
                "detail": f"branch {branch!r} does not exist",
            },
        )

    if merge_req.squash:
        # Stage all commits as a single diff, then commit manually.
        rc_m, merge_out, merge_err = await _run_git(
            "merge", "--squash", branch,
            cwd=project_path,
            timeout=60.0,
        )
        if rc_m != 0:
            await _run_git("reset", "--merge", cwd=project_path)
            raise HTTPException(
                status_code=409,
                detail={
                    "merged": False,
                    "conflict": True,
                    "detail": (merge_err or merge_out)[:2000],
                },
            )
        commit_msg = merge_req.message or f"Squash merge {branch} into {base}"
        rc_c, _, commit_err = await _run_git(
            "commit", "-m", commit_msg,
            cwd=project_path,
            timeout=30.0,
        )
        if rc_c != 0:
            await _run_git("reset", "--merge", cwd=project_path)
            raise HTTPException(
                status_code=409,
                detail={
                    "merged": False,
                    "conflict": True,
                    "detail": commit_err[:2000],
                },
            )
    else:
        # Standard --no-ff merge.
        merge_args: list[str] = ["merge", "--no-ff"]
        if merge_req.message:
            merge_args += ["-m", merge_req.message]
        merge_args.append(branch)

        rc_m, merge_out, merge_err = await _run_git(
            *merge_args,
            cwd=project_path,
            timeout=60.0,
        )
        if rc_m != 0:
            # Abort so the main tree is left clean.
            await _run_git("merge", "--abort", cwd=project_path)
            raise HTTPException(
                status_code=409,
                detail={
                    "merged": False,
                    "conflict": True,
                    "detail": (merge_err or merge_out)[:2000],
                },
            )

    # Merge succeeded -- clean up.
    try:
        await remove_worktree(project_path, session_id, delete_branch=True)
    except WorktreeError as exc:
        # Non-fatal: cleanup failure doesn't invalidate the merge.
        logging.getLogger(__name__).warning(
            "worktree: post-merge cleanup failed for session=%s: %s", session_id, exc
        )

    return WorktreeMergeResponse(merged=True, into=base, branch=branch).model_dump()


@router.delete("/sessions/{session_id}/worktree")
async def discard_worktree(
    session_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Discard the Forge worktree and its branch without merging (human gate: reject)."""
    from artemis.dev_projects.worktree import remove_worktree

    try:
        dev_session = await repo.get_session(session, session_id)
        project = await repo.get_project(session, dev_session.project_id)
    except ValueError:
        raise not_found(f"Session {session_id} not found", "session_not_found")  # noqa: B904

    await remove_worktree(project.path, session_id, delete_branch=True)
    return WorktreeDiscardResponse(discarded=True).model_dump()


# ─────────────────────────────────────────────────────────────────────────────


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
