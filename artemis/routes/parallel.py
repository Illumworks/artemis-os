"""Parallel chat session allocation.

POST /api/parallel/sessions
  body:  { "count": 2 | 3 | 4 }
  reply: { "pane_ids": ["parallel-<uuid>-pane-0", ...] }

Each pane_id is a floating-artemis session ID the caller should
  1. POST /api/floating-artemis/sessions  (session_id = pane_id)
  2. connect WS at /ws/floating-artemis/<pane_id>

This endpoint is intentionally thin — it allocates IDs and
creates the FA sessions in a single roundtrip so the frontend
doesn't need to issue N+1 requests.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.floating_artemis import repository as fa_repo
from artemis.marketing.routes._auth import require_token

router = APIRouter(
    prefix="/api/parallel",
    tags=["parallel"],
    dependencies=[Depends(require_token)],
)


class ParallelSessionsRequest(BaseModel):
    count: int = Field(default=2, ge=2, le=4)


@router.post("/sessions", status_code=201)
async def allocate_parallel_sessions(
    body: ParallelSessionsRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Allocate *count* floating-artemis sessions for parallel chat panes.

    Each session is created (or silently reused if the ID collides) and its
    ID is returned so the frontend can connect immediately.
    """
    run_id = uuid.uuid4().hex[:12]
    pane_ids: list[str] = []

    for i in range(body.count):
        pane_id = f"parallel-{run_id}-pane-{i}"
        try:
            await fa_repo.get_session_by_id(session, pane_id)
        except ValueError:
            await fa_repo.create_session(
                session,
                session_id=pane_id,
                owner_user_id=None,
                title=f"Parallel Pane {i + 1}",
                metadata={"source": "parallel", "pane_index": i, "run_id": run_id},
            )
        pane_ids.append(pane_id)

    await session.commit()
    return {"pane_ids": pane_ids, "run_id": run_id}
