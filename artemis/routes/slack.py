"""Slack API routes — /api/slack.

GET  /api/slack/signals                       — signal counts for the Focus Rail.
GET  /api/slack/signals?refresh=1             — force-refresh, bypass 60-s cache.
GET  /api/slack/signals/mentions              — top 20 unresolved mentions (J9).
POST /api/slack/signals/mentions/{id}/resolve — mark a mention resolved (J9).

GET /signals always returns 200. ``connected: false`` is a valid empty state the
frontend renders as "not connected" — never 4xx for that condition.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations.slack import signals
from artemis.integrations.slack.triage import list_unresolved_mentions, resolve_mention

router = APIRouter(prefix="/api/slack", tags=["slack"])


@router.get("/signals/mentions")
async def get_mentions(
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    """Return the most recent unresolved Slack mentions (triage queue).

    Ordered by ts DESC. Does not paginate beyond `limit`.
    """
    return await list_unresolved_mentions(session, limit=limit)


@router.post("/signals/mentions/{event_id}/resolve")
async def resolve_mention_route(
    event_id: str,
    body: dict[str, str] = Body(default={}),  # noqa: B008
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    """Mark a mention resolved.

    Idempotent — resolving an already-resolved row is a no-op (no error).
    Optional body: ``{"action": "replied" | "ignored"}`` (not persisted yet;
    reserved for future audit trail).

    Returns ``{ok: true, new_total_unresolved: <int>}``.
    Raises 404 when the event_id doesn't exist.
    """
    try:
        _, new_total = await resolve_mention(session, event_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"mention {event_id!r} not found")  # noqa: B904
    return {"ok": True, "new_total_unresolved": new_total}


@router.get("/signals")
async def get_signals(
    refresh: int = Query(default=0),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    return await signals.get_slack_signals(session, force_refresh=bool(refresh))
