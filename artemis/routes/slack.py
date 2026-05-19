"""Slack API routes — /api/slack.

GET  /api/slack/signals                       — signal counts for the Focus Rail.
GET  /api/slack/signals?refresh=1             — force-refresh, bypass 60-s cache.
GET  /api/slack/signals/mentions              — top 20 unresolved mentions (J9).
GET  /api/slack/signals/mentions?include=direct,channel  — override type filter.
POST /api/slack/signals/mentions/{id}/resolve — mark a mention resolved (J9).

GET /signals always returns 200. ``connected: false`` is a valid empty state the
frontend renders as "not connected" — never 4xx for that condition.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.crypto import decrypt_credentials
from artemis.integrations.slack import signals
from artemis.integrations.slack.triage import list_unresolved_mentions, resolve_mention

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/slack", tags=["slack"])


@router.get("/signals/mentions")
async def get_mentions(
    limit: int = Query(default=20, ge=1, le=100),
    include: str = Query(default="direct"),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    """Return the most recent unresolved Slack mentions (triage queue).

    Filtered to ``mention_type = 'direct'`` by default so @channel / @here
    broadcasts don't pollute the personal reply queue.

    ``include`` accepts a comma-separated list of types to widen the filter,
    e.g. ``?include=direct,channel`` to also show @channel pings.

    Ordered by ts DESC. Does not paginate beyond ``limit``.
    Name resolution is attempted when the Slack integration is active; raw IDs
    are returned when no token is available.
    """
    include_types = [t.strip() for t in include.split(",") if t.strip()]
    if not include_types:
        include_types = ["direct"]

    # Attempt to get Slack access token for name resolution
    token: str | None = None
    try:
        integrations = await repo.list_active(session, provider="slack")
        if integrations:
            creds = decrypt_credentials(bytes(integrations[0].encrypted_credentials))
            token = str(creds.get("access_token", "")) or None
    except Exception:
        logger.debug("get_mentions: could not resolve Slack token for name lookup")

    return await list_unresolved_mentions(session, limit=limit, include_types=include_types, token=token)


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
