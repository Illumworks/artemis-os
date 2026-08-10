"""Repository helpers for Dev Projects."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.dev_projects.models import DevAnnotation, DevMessage, DevProject, DevSession


def _now() -> datetime:
    return datetime.now(UTC)


def _project_read(row: DevProject) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "path": row.path,
        "last_opened_at": row.last_opened_at,
        "archived_at": row.archived_at,
        "metadata": row.metadata_ or {},
    }


async def create_project(
    session: AsyncSession, *, name: str, path: str, metadata: dict[str, Any] | None = None
) -> DevProject:
    row = DevProject(
        name=name, path=str(Path(path).expanduser().resolve()), metadata_=metadata or {}
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_project(session: AsyncSession, project_id: int) -> DevProject:
    row = await session.get(DevProject, project_id)
    if row is None:
        raise ValueError(f"DevProject {project_id} not found")
    return row


async def list_projects(session: AsyncSession) -> list[DevProject]:
    result = await session.execute(
        select(DevProject).order_by(
            DevProject.archived_at.is_not(None),
            DevProject.last_opened_at.desc(),
            DevProject.id.desc(),
        )
    )
    return list(result.scalars().all())


async def update_project(
    session: AsyncSession,
    project_id: int,
    *,
    name: str | None = None,
    archived: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> DevProject:
    row = await get_project(session, project_id)
    if name is not None:
        row.name = name
    if metadata is not None:
        row.metadata_ = metadata
    if archived is not None:
        row.archived_at = _now() if archived else None
    row.last_opened_at = _now()
    await session.flush()
    await session.refresh(row)
    return row


async def archive_project(session: AsyncSession, project_id: int) -> DevProject:
    return await update_project(session, project_id, archived=True)


async def delete_project(session: AsyncSession, project_id: int) -> None:
    row = await get_project(session, project_id)
    await session.delete(row)


async def create_session(
    session: AsyncSession,
    *,
    project_id: int,
    provider: str = "claude-code",
    model: str | None = None,
    title: str | None = None,
    fork_of: int | None = None,
    fork_at_message: int | None = None,
    forge_mode: str | None = None,
) -> DevSession:
    await get_project(session, project_id)
    row = DevSession(
        project_id=project_id,
        provider=provider,
        model=model,
        title=title,
        fork_of=fork_of,
        fork_at_message=fork_at_message,
        notes=[],
        forge_mode=forge_mode if forge_mode in ("read", "write") else None,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def get_session(session: AsyncSession, session_id: int) -> DevSession:
    row = await session.get(DevSession, session_id)
    if row is None:
        raise ValueError(f"DevSession {session_id} not found")
    return row


async def list_sessions(session: AsyncSession, project_id: int) -> list[tuple[DevSession, int]]:
    await get_project(session, project_id)
    count_expr = func.count(DevMessage.id).label("message_count")
    result = await session.execute(
        select(DevSession, count_expr)
        .outerjoin(DevMessage, DevMessage.session_id == DevSession.id)
        .where(DevSession.project_id == project_id)
        .group_by(DevSession.id)
        .order_by(
            DevSession.archived_at.is_not(None),
            DevSession.pinned.desc(),
            DevSession.last_active_at.desc(),
        )
    )
    return [(row[0], int(row[1] or 0)) for row in result.all()]


_UNSET: object = object()


async def update_session(
    session: AsyncSession,
    session_id: int,
    *,
    title: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    bypass_permissions: bool | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    forge_mode: object = _UNSET,
) -> DevSession:
    row = await get_session(session, session_id)
    if title is not None:
        row.title = title
    if provider is not None:
        row.provider = provider
    if model is not None:
        row.model = model
    if bypass_permissions is not None:
        row.bypass_permissions = bypass_permissions
    if pinned is not None:
        if pinned:
            await session.execute(
                update(DevSession)
                .where(DevSession.project_id == row.project_id, DevSession.id != row.id)
                .values(pinned=False)
            )
        row.pinned = pinned
    if archived is not None:
        row.archived_at = _now() if archived else None
    if forge_mode is not _UNSET:
        row.forge_mode = forge_mode if forge_mode in ("read", "write") else None
    row.last_active_at = _now()
    await session.flush()
    await session.refresh(row)
    return row


async def archive_session(session: AsyncSession, session_id: int) -> DevSession:
    return await update_session(session, session_id, archived=True)


async def delete_session(session: AsyncSession, session_id: int) -> None:
    row = await get_session(session, session_id)
    await session.delete(row)


async def touch_session(session: AsyncSession, session_id: int) -> None:
    row = await get_session(session, session_id)
    row.last_active_at = _now()
    project = await get_project(session, row.project_id)
    project.last_opened_at = _now()
    await session.flush()


async def add_message(
    session: AsyncSession, *, session_id: int, role: str, content: list[dict[str, Any]]
) -> DevMessage:
    await get_session(session, session_id)
    msg = DevMessage(session_id=session_id, role=role, content=content)
    session.add(msg)
    await touch_session(session, session_id)
    await session.flush()
    await session.refresh(msg)
    return msg


async def list_messages(
    session: AsyncSession,
    session_id: int,
    *,
    limit: int = 50,
    cursor: int | None = None,
) -> list[DevMessage]:
    query: Select[tuple[DevMessage]] = (
        select(DevMessage)
        .where(DevMessage.session_id == session_id)
        .order_by(DevMessage.id.asc())
        .limit(limit)
    )
    if cursor is not None:
        query = query.where(DevMessage.id > cursor)
    result = await session.execute(query)
    return list(result.scalars().all())


async def fork_session(
    session: AsyncSession, *, source_session_id: int, at_message_id: int
) -> DevSession:
    source = await get_session(session, source_session_id)
    fork = await create_session(
        session,
        project_id=source.project_id,
        provider=source.provider,
        model=source.model,
        title=f"{source.title or 'Untitled'} fork",
        fork_of=source.id,
        fork_at_message=at_message_id,
    )
    result = await session.execute(
        select(DevMessage)
        .where(DevMessage.session_id == source_session_id, DevMessage.id <= at_message_id)
        .order_by(DevMessage.id.asc())
    )
    for msg in result.scalars():
        session.add(DevMessage(session_id=fork.id, role=msg.role, content=msg.content))
    await session.flush()
    await session.refresh(fork)
    return fork


async def add_annotation(
    session: AsyncSession, *, session_id: int, url: str | None, note: str
) -> DevAnnotation:
    dev_session = await get_session(session, session_id)
    annotation = DevAnnotation(session_id=session_id, url=url, note=note)
    session.add(annotation)
    notes = list(dev_session.notes or [])
    notes.append({"url": url, "note": note, "created_at": _now().isoformat()})
    dev_session.notes = notes
    dev_session.last_active_at = _now()
    await session.flush()
    await session.refresh(annotation)
    return annotation


async def list_annotations(session: AsyncSession, session_id: int) -> list[DevAnnotation]:
    await get_session(session, session_id)
    result = await session.execute(
        select(DevAnnotation)
        .where(DevAnnotation.session_id == session_id)
        .order_by(DevAnnotation.created_at.desc(), DevAnnotation.id.desc())
    )
    return list(result.scalars().all())


async def delete_annotation(session: AsyncSession, annotation_id: int) -> None:
    row = await session.get(DevAnnotation, annotation_id)
    if row is None:
        raise ValueError(f"DevAnnotation {annotation_id} not found")
    await session.delete(row)


def project_to_dict(row: DevProject) -> dict[str, Any]:
    return _project_read(row)
