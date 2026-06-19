"""Repository helpers for ProjectWorkspaceMemory.

Exposes four async functions:

  get_workspace_memory   -- fetch the drawer or return None
  ensure_workspace_memory -- fetch or create an empty drawer
  update_workspace_memory -- newest-wins update of plan/file_map/progress/open_threads
  append_decision         -- append-only push to the decisions log (LOSSLESS)

IMPORTANT: decisions are append-only and are NEVER deleted (lossless memory rule).
There is intentionally no delete or prune function in this module.

JSONB mutation note: SQLAlchemy does NOT detect in-place list/dict mutations on
JSONB columns. After appending to decisions we use flag_modified() to force the
UPDATE — this is a known footgun in this repo (see memory note
feedback-node-states-flag-modified.md).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from artemis.dev_projects.models import ProjectWorkspaceMemory


async def get_workspace_memory(
    session: AsyncSession,
    project_id: int,
) -> ProjectWorkspaceMemory | None:
    """Return the workspace memory drawer for *project_id*, or None if absent."""
    result = await session.execute(
        select(ProjectWorkspaceMemory).where(
            ProjectWorkspaceMemory.project_id == project_id
        )
    )
    return result.scalar_one_or_none()


async def ensure_workspace_memory(
    session: AsyncSession,
    project_id: int,
) -> ProjectWorkspaceMemory:
    """Return the workspace memory drawer, creating an empty one if it does not exist."""
    row = await get_workspace_memory(session, project_id)
    if row is not None:
        return row

    row = ProjectWorkspaceMemory(
        project_id=project_id,
        decisions=[],
        file_map={},
        open_threads=[],
    )
    session.add(row)
    await session.flush()  # assigns the PK without committing the outer transaction
    return row


async def update_workspace_memory(
    session: AsyncSession,
    project_id: int,
    *,
    plan: str | None = None,
    file_map: dict[str, Any] | None = None,
    progress: str | None = None,
    open_threads: list[dict[str, Any]] | None = None,
) -> ProjectWorkspaceMemory:
    """Overwrite only the provided (non-None) fields; bumps updated_at.

    Uses newest-wins semantics: the caller supplies the full replacement value
    for each field being updated (no merge logic here).
    """
    row = await ensure_workspace_memory(session, project_id)

    if plan is not None:
        row.plan = plan
    if file_map is not None:
        row.file_map = file_map
    if progress is not None:
        row.progress = progress
    if open_threads is not None:
        row.open_threads = open_threads

    row.updated_at = datetime.now(UTC)
    return row


async def append_decision(
    session: AsyncSession,
    project_id: int,
    text: str,
) -> ProjectWorkspaceMemory:
    """Append one entry to the decisions log (append-only, never pruned).

    Each entry has shape {"ts": "<iso8601>", "text": "<str>"}.

    flag_modified() is called after mutation so SQLAlchemy emits the UPDATE
    even though we mutated the list in place (JSONB columns require this).
    """
    row = await ensure_workspace_memory(session, project_id)

    entry: dict[str, str] = {
        "ts": datetime.now(UTC).isoformat(),
        "text": text,
    }
    # Mutate in place then flag so SQLAlchemy tracks the change.
    row.decisions = list(row.decisions)  # ensure a new list object
    row.decisions.append(entry)
    flag_modified(row, "decisions")

    row.updated_at = datetime.now(UTC)
    return row
