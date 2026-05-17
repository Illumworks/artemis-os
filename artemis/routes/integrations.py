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
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_slack_config
from artemis.integrations.models import _KNOWN_PROVIDERS, Integration
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


def _redirect_uri() -> str:
    return os.environ.get(
        "SLACK_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/slack/oauth/callback",
    )


async def _slack_provider_from_session(session: AsyncSession) -> SlackProvider:
    """Build a SlackProvider using DB-resolved credentials (env fallback)."""
    try:
        cfg = await resolve_slack_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Slack credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc
    return SlackProvider(
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        redirect_uri=_redirect_uri(),
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
        try:
            provider = await _slack_provider_from_session(session)
            await provider.revoke(integration)
        except Exception:
            logger.warning("Slack revoke call failed; marking revoked locally anyway")

    await repo.mark_revoked(session, integration_id)
    await session.commit()


@router.get("/slack/oauth/start")
async def slack_oauth_start(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, str]:
    try:
        cfg = await resolve_slack_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Slack credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc

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
        f"?client_id={cfg.client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={_redirect_uri()}"
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

    provider = await _slack_provider_from_session(session)
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
    provider = await _slack_provider_from_session(session)
    ok = await provider.verify(integration)
    if ok:
        await repo.mark_verified(session, integration.id)
        await session.commit()
    return {"ok": ok, "workspace": integration.display_name or integration.workspace_id}


# ── Provider credential config (J1b) ─────────────────────────────────────────


class ProviderConfigOut(BaseModel):
    provider: str
    configured_keys: dict[str, bool]
    ever_configured: bool


class ProviderConfigIn(BaseModel):
    model_config = ConfigDict(extra="allow")


@router.get("/providers/{provider}/config", response_model=ProviderConfigOut)
async def get_provider_config(
    provider: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ProviderConfigOut:
    if provider not in _KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider!r}")
    config = await repo.get_provider_config(session, provider)
    ever_configured = config is not None
    configured_keys = {k: bool(v and str(v).strip()) for k, v in (config or {}).items()}
    return ProviderConfigOut(
        provider=provider,
        configured_keys=configured_keys,
        ever_configured=ever_configured,
    )


@router.post("/providers/{provider}/config", response_model=ProviderConfigOut)
async def set_provider_config(
    provider: str,
    body: ProviderConfigIn,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ProviderConfigOut:
    if provider not in _KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider!r}")
    payload: dict[str, object] = dict(body.model_extra or {})
    if not payload:
        raise HTTPException(status_code=422, detail="Request body must contain at least one field.")
    await repo.upsert_provider_config(session, provider, payload)
    await session.commit()
    config = await repo.get_provider_config(session, provider)
    configured_keys = {k: bool(v and str(v).strip()) for k, v in (config or {}).items()}
    return ProviderConfigOut(
        provider=provider,
        configured_keys=configured_keys,
        ever_configured=True,
    )


@router.delete("/providers/{provider}/config", status_code=204)
async def delete_provider_config(
    provider: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> None:
    if provider not in _KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider!r}")
    await repo.delete_provider_config(session, provider)
    await session.commit()
