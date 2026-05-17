"""Integrations router — /api/integrations.

Endpoints:
  GET    /api/integrations                          — list active integrations
  DELETE /api/integrations/{id}                     — revoke an integration
  GET    /api/integrations/slack/oauth/start        — Slack OAuth redirect URL
  GET    /api/integrations/slack/oauth/callback     — Slack OAuth callback
  GET    /api/integrations/slack/verify             — auth.test ping
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.models import Integration
from artemis.integrations.slack.provider import SlackProvider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])

# In-memory state store for OAuth (single-process; sufficient for V1).
_oauth_states: dict[str, str] = {}


class IntegrationOut(BaseModel):
    id: int
    provider: str
    workspace_id: str
    display_name: str | None
    connected_at: datetime
    status: str

    model_config = {"from_attributes": True}


def _slack_provider() -> SlackProvider:
    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    client_secret = os.environ.get("SLACK_CLIENT_SECRET", "")
    redirect_uri = os.environ.get(
        "SLACK_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/slack/oauth/callback",
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Slack app credentials not configured (SLACK_CLIENT_ID / SLACK_CLIENT_SECRET).",
        )
    return SlackProvider(
        client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri
    )


# ── Routes ────────────────────────────────────────────────────────────────────


@router.get("", response_model=list[IntegrationOut])
async def list_integrations(
    provider: str | None = Query(default=None),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> list[Integration]:
    return await repo.list_active(session, provider=provider)


@router.delete("/{integration_id}", status_code=204)
async def revoke_integration(
    integration_id: int,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> None:
    try:
        integration = await repo.get_by_id(session, integration_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if integration.provider == "slack":
        provider = _slack_provider()
        try:
            await provider.revoke(integration)
        except Exception:
            logger.warning("Slack revoke call failed; marking revoked locally anyway")

    await repo.mark_revoked(session, integration_id)
    await session.commit()


@router.get("/slack/oauth/start")
async def slack_oauth_start() -> dict[str, str]:
    client_id = os.environ.get("SLACK_CLIENT_ID", "")
    redirect_uri = os.environ.get(
        "SLACK_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/slack/oauth/callback",
    )
    if not client_id:
        raise HTTPException(status_code=503, detail="SLACK_CLIENT_ID not configured.")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = "pending"

    scopes = ",".join(
        [
            "chat:write",
            "chat:write.public",
            "channels:read",
            "channels:history",
            "groups:read",
            "groups:history",
            "im:read",
            "im:history",
            "im:write",
            "users:read",
            "reactions:write",
            "app_mentions:read",
        ]
    )
    url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return {"url": url}


@router.get("/slack/oauth/callback")
async def slack_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> RedirectResponse:
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    del _oauth_states[state]

    provider = _slack_provider()
    try:
        integration_row = await provider.connect(code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await repo.upsert_integration(
        session,
        provider=integration_row.provider,
        workspace_id=integration_row.workspace_id,
        encrypted_credentials=integration_row.encrypted_credentials,
        display_name=integration_row.display_name,
        bot_user_id=integration_row.bot_user_id,
        scopes=integration_row.scopes,
    )
    await session.commit()

    return RedirectResponse(url="/?slack_connected=1", status_code=302)


@router.get("/slack/verify")
async def slack_verify(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, bool | str]:
    """Ping Slack auth.test for the active workspace integration."""
    try:
        rows = await repo.list_active(session, provider="slack")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=404, detail="No active Slack integration.")

    integration = rows[0]
    provider = _slack_provider()
    ok = await provider.verify(integration)
    if ok:
        await repo.mark_verified(session, integration.id)
        await session.commit()
    return {"ok": ok, "workspace": integration.display_name or integration.workspace_id}
