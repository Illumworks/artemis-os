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
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.db as db
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import (
    MissingProviderConfigError,
    resolve_gcal_config,
    resolve_slack_config,
)
from artemis.integrations.gcal.provider import GCalProvider
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


# ── GCal OAuth ────────────────────────────────────────────────────────────────

_GCAL_SCOPE = " ".join(
    [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/userinfo.email",
        "openid",
    ]
)


def _gcal_redirect_uri() -> str:
    return os.environ.get(
        "GCAL_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/gcal/oauth/callback",
    )


async def _gcal_provider_from_session(session: AsyncSession) -> GCalProvider:
    try:
        cfg = await resolve_gcal_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"GCal credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc
    return GCalProvider(
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        redirect_uri=_gcal_redirect_uri(),
    )


@router.get("/gcal/oauth/start")
async def gcal_oauth_start(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, str]:
    try:
        cfg = await resolve_gcal_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"GCal credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = "pending"

    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={cfg.client_id}"
        f"&redirect_uri={_gcal_redirect_uri()}"
        f"&response_type=code"
        f"&scope={_GCAL_SCOPE}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&state={state}"
    )
    return {"url": url}


@router.get("/jira/oauth/start")
async def jira_oauth_start_compat(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, str]:
    """Jira uses basic auth (site_url + email + api_token) — no OAuth dance.

    The frontend Connect button hits this endpoint expecting an OAuth URL.
    Instead we verify the saved credentials hit a real Atlassian site,
    create the integration row, and return a redirect URL with
    ?jira_connected=1 so the existing front-end flow Just Works for Jira
    like it does for OAuth providers.
    """
    from artemis.integrations import crypto
    from artemis.integrations.config_resolver import resolve_jira_config
    from artemis.integrations.jira.client import JiraAPIError, JiraClient

    try:
        cfg = await resolve_jira_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Jira credentials incomplete: {', '.join(exc.missing_fields)}",
        ) from exc

    client = JiraClient(site_url=cfg.site_url, email=cfg.email, api_token=cfg.api_token)
    try:
        # Use the strict /myself endpoint — Atlassian Cloud sometimes returns
        # 200-empty from search/jql for limited/unauthenticated callers, so a
        # successful search isn't a reliable signal of valid creds.
        await client.get_overview(project_key="", max_items=1)
    except JiraAPIError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Jira rejected your credentials ({exc}). "
                "Double-check: (1) the Site URL is the exact Atlassian domain you "
                "see in your browser, (2) the Email is the account that created the "
                "API Token, (3) the Token hasn't expired."
            ),
        ) from exc

    creds_blob = crypto.encrypt_credentials(
        {"site_url": cfg.site_url, "email": cfg.email, "api_token": cfg.api_token}
    )
    await repo.upsert_integration(
        session,
        provider="jira",
        workspace_id=cfg.site_url,
        display_name=cfg.site_url.replace("https://", "").rstrip("/"),
        encrypted_credentials=creds_blob,
        scopes=None,
    )
    await session.commit()

    return {"url": "/?jira_connected=1"}


@router.get("/gcal/oauth/callback")
async def gcal_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> RedirectResponse:
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    del _oauth_states[state]

    provider = await _gcal_provider_from_session(session)
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
    )
    await session.commit()

    return RedirectResponse(url="/?gcal_connected=1", status_code=302)


@router.get("/gcal/verify")
async def gcal_verify(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, bool | str]:
    """List calendars as a smoke-test for the active GCal integration."""
    try:
        rows = await repo.list_active(session, provider="gcal")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=404, detail="No active Google Calendar integration.")

    integration = rows[0]
    provider = await _gcal_provider_from_session(session)
    ok = await provider.verify(integration)
    if ok:
        await repo.mark_verified(session, integration.id)
        await session.commit()
    return {"ok": ok, "account": integration.display_name or integration.workspace_id}


# ── Provider credential config (J1b) ─────────────────────────────────────────


class ProviderConfigOut(BaseModel):
    provider: str
    configured_keys: dict[str, bool]
    ever_configured: bool


class ProviderConfigIn(BaseModel):
    model_config = ConfigDict(extra="allow")


# Env-var fallback: when a credential is already set in ~/.artemis/.env or the
# project .env, the integration counts as configured even if the DB-stored row
# doesn't exist yet. This keeps Connect buttons clickable for operators who
# pasted secrets into env files directly (the original Node app's pattern).
_PROVIDER_ENV_FIELDS: dict[str, dict[str, str]] = {
    "slack": {
        "client_id": "SLACK_CLIENT_ID",
        "client_secret": "SLACK_CLIENT_SECRET",
        "signing_secret": "SLACK_SIGNING_SECRET",
    },
    "gcal": {
        "client_id": "GCAL_CLIENT_ID",
        "client_secret": "GCAL_CLIENT_SECRET",
    },
    "anthropic": {"api_key": "ANTHROPIC_API_KEY"},
    "openai": {"api_key": "OPENAI_API_KEY"},
    "gemini": {"api_key": "GEMINI_API_KEY"},
    "openrouter": {"api_key": "OPENROUTER_API_KEY"},
}


@router.get("/providers/{provider}/config", response_model=ProviderConfigOut)
async def get_provider_config(
    provider: str,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> ProviderConfigOut:
    if provider not in _KNOWN_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider!r}")
    config = await repo.get_provider_config(session, provider)
    configured_keys: dict[str, bool] = {
        k: bool(v and str(v).strip()) for k, v in (config or {}).items()
    }
    # Merge env-var presence so DB-less but env-configured providers are
    # reported as configured (Connect button stays clickable).
    for field_name, env_var in _PROVIDER_ENV_FIELDS.get(provider, {}).items():
        if os.environ.get(env_var):
            configured_keys[field_name] = True
    ever_configured = any(configured_keys.values())
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


# ── Granola integration (J6a) ─────────────────────────────────────────────────
#
# Two connect modes:
#   POST /api/integrations/granola/connect-local  — reads supabase.json; no OAuth
#   GET  /api/integrations/granola/oauth/start    — PKCE OAuth redirect
#   GET  /api/integrations/granola/oauth/callback — code exchange + upsert
#
# The frontend tries connect-local first; falls back to oauth/start if the
# local state file is absent or the token is invalid.

_GRANOLA_PKCE_TTL_S = 600  # 10 minutes

# In-memory: state → {verifier, redirect_uri, created_at}
_granola_pkce_store: dict[str, dict[str, object]] = {}


def _sweep_granola_pkce(now: float | None = None) -> None:
    ts = now if now is not None else time.time()
    expired = [
        k
        for k, v in _granola_pkce_store.items()
        if ts - float(str(v["created_at"] or 0)) > _GRANOLA_PKCE_TTL_S
    ]
    for k in expired:
        del _granola_pkce_store[k]


def _granola_redirect_uri(request: Request) -> str:
    """Always canonicalise host to localhost so the same client_id works from any LAN address."""
    import re as _re

    host_header = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:8000"
    )
    port_match = _re.search(r":(\d+)$", host_header)
    port = f":{port_match.group(1)}" if port_match else ""
    return f"http://localhost{port}/api/integrations/granola/oauth/callback"


@router.post("/granola/connect-local")
async def granola_connect_local(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    """Read Granola desktop-app supabase.json, verify, and create integration row.

    Returns {"ok": true, "display_name": "..."} on success.
    Returns HTTP 400 with {"error": "...", "fallback": "oauth"} when not available.
    """
    from artemis.integrations.granola.client import GranolaAPIError
    from artemis.integrations.granola.local_state import read_local_token
    from artemis.integrations.granola.provider import GranolaProvider

    token = read_local_token()
    if not token:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Granola.app not detected. Make sure it is installed and you are signed in, or use OAuth to connect.",
                "fallback": "oauth",
            },
        )

    provider = GranolaProvider()
    try:
        integration_row = await provider.connect_local(access_token=token)
    except GranolaAPIError as exc:
        if exc.status == 401:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": "Granola.app token is invalid or expired. Please sign in to the Granola app and try again.",
                    "fallback": "oauth",
                },
            ) from exc
        raise HTTPException(
            status_code=502,
            detail={"error": f"Granola API error: {exc}", "fallback": "oauth"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={"error": f"Could not connect to Granola: {exc}", "fallback": "oauth"},
        ) from exc

    await repo.upsert_integration(
        session,
        provider=integration_row.provider,
        workspace_id=integration_row.workspace_id,
        encrypted_credentials=integration_row.encrypted_credentials,
        display_name=integration_row.display_name,
        scopes=integration_row.scopes,
        metadata=integration_row.metadata_,
    )
    await session.commit()

    return {"ok": True, "display_name": integration_row.display_name}


@router.get("/granola/oauth/start")
async def granola_oauth_start(
    request: Request,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, str]:
    """Return a Granola OAuth PKCE authorization URL.

    Performs dynamic client registration (RFC 7591) if no client_id is stored.
    """
    import base64
    import hashlib
    import os as _os

    from artemis.integrations.config_resolver import resolve_granola_config
    from artemis.integrations.granola.client import (
        GRANOLA_AUTH_ENDPOINT,
        GRANOLA_REGISTER_ENDPOINT,
        GRANOLA_RESOURCE,
        GRANOLA_SCOPES,
    )

    cfg = await resolve_granola_config(session)
    redirect_uri = _granola_redirect_uri(request)
    client_id = cfg.client_id
    client_secret = cfg.client_secret

    # Dynamic client registration if client_id absent or redirect_uri unknown
    if not client_id:
        async with httpx.AsyncClient(timeout=15) as http:
            dcr_resp = await http.post(
                GRANOLA_REGISTER_ENDPOINT,
                json={
                    "client_name": "Artemis",
                    "redirect_uris": [redirect_uri],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": GRANOLA_SCOPES,
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
        if not dcr_resp.is_success:
            raise HTTPException(
                status_code=502, detail=f"Granola DCR failed: {dcr_resp.status_code}"
            )
        dcr = dcr_resp.json()
        client_id = dcr.get("client_id", "")
        client_secret = dcr.get("client_secret", "")
        if not client_id:
            raise HTTPException(status_code=502, detail="Granola DCR response missing client_id")

        # Persist to provider config so we don't re-register next time
        await repo.upsert_provider_config(
            session,
            "granola",
            {
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
        await session.commit()

    # Generate PKCE pair
    verifier_bytes = _os.urandom(32)
    verifier = base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )

    state = secrets.token_urlsafe(32)
    _sweep_granola_pkce()
    _granola_pkce_store[state] = {
        "verifier": verifier,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "created_at": time.time(),
    }

    params = (
        f"response_type=code"
        f"&client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={GRANOLA_SCOPES.replace(' ', '%20')}"
        f"&state={state}"
        f"&code_challenge={challenge}"
        f"&code_challenge_method=S256"
        f"&resource={GRANOLA_RESOURCE}"
    )
    return {"url": f"{GRANOLA_AUTH_ENDPOINT}?{params}"}


@router.get("/granola/oauth/callback")
async def granola_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> RedirectResponse:
    """Exchange OAuth code for tokens, create integration row, redirect to UI."""
    from artemis.integrations.granola.client import (
        GRANOLA_RESOURCE,
        GRANOLA_TOKEN_ENDPOINT,
        GranolaClient,
    )

    _sweep_granola_pkce()
    entry = _granola_pkce_store.pop(state, None)
    if not entry:
        return RedirectResponse(url="/?granola_error=state_mismatch", status_code=302)

    verifier = str(entry["verifier"])
    redirect_uri = str(entry["redirect_uri"])
    client_id = str(entry["client_id"])

    from artemis.integrations.config_resolver import resolve_granola_config

    cfg = await resolve_granola_config(session)
    client_secret = cfg.client_secret

    # Exchange code for tokens
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": GRANOLA_RESOURCE,
    }
    if client_secret:
        data["client_secret"] = client_secret

    async with httpx.AsyncClient(timeout=15) as http:
        token_resp = await http.post(
            GRANOLA_TOKEN_ENDPOINT,
            data=data,
            headers={"Accept": "application/json"},
        )

    if not token_resp.is_success:
        return RedirectResponse(
            url=f"/?granola_error=token_{token_resp.status_code}", status_code=302
        )

    body = token_resp.json()
    access_token = body.get("access_token", "")
    if not access_token:
        return RedirectResponse(url="/?granola_error=no_access_token", status_code=302)

    refresh_token = body.get("refresh_token", "")
    expires_in = int(body.get("expires_in", 3600))
    expires_at = time.time() + expires_in

    # Fetch account info for display_name / workspace_id
    client = GranolaClient(access_token=access_token, refresh_token=refresh_token)
    try:
        account_info = await client.get_account_info()
    except Exception:
        account_info = {}

    email = account_info.get("email") or account_info.get("userEmail") or "granola-user"
    display_name = account_info.get("name") or account_info.get("displayName") or str(email)

    from artemis.integrations.crypto import encrypt_credentials

    credentials = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_mode": "oauth",
    }
    encrypted = encrypt_credentials(credentials)

    await repo.upsert_integration(
        session,
        provider="granola",
        workspace_id=str(email),
        display_name=str(display_name),
        encrypted_credentials=encrypted,
        scopes=["openid", "profile", "email", "offline_access"],
        metadata={"auth_mode": "oauth"},
    )
    await session.commit()

    return RedirectResponse(url="/?granola_connected=1", status_code=302)
