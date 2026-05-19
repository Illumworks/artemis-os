"""Slack API routes — /api/slack.

GET /api/slack/signals          — Slack signal counts for the Focus Rail.
GET /api/slack/signals?refresh=1 — force-refresh, bypass 60-s cache.

Always returns 200.  ``connected: false`` is a valid empty state the frontend
renders as "not connected" — never 4xx for that condition.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations.slack import signals

router = APIRouter(prefix="/api/slack", tags=["slack"])


@router.get("/signals")
async def get_signals(
    refresh: int = Query(default=0),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    return await signals.get_slack_signals(session, force_refresh=bool(refresh))
