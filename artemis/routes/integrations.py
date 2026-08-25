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
from artemis.google_docs.client import build_google_oauth_start_url
from artemis.google_integration import (
    complete_google_oauth,
    register_google_oauth_state,
    resolve_google_oauth_client_config,
    scopes_for_google_purpose,
)
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.integrations import repository as repo
from artemis.integrations.config_resolver import (
    MissingProviderConfigError,
    resolve_gcal_config,
    resolve_slack_config,
)
from artemis.integrations.gcal.provider import GCalProvider
from artemis.integrations.models import _KNOWN_PROVIDERS, Integration
from artemis.integrations.slack.provider import SlackProvider
from artemis.marketing.routes._auth import require_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


async def live_scopes_for_token(access_token: str) -> frozenset[str] | None:
    """Read a Slack token's ACTUAL granted scopes from the ``x-oauth-scopes`` header.

    This is the only authoritative source. ``integrations.scopes`` records what
    was granted when the row was written and drifts silently: a Slack app
    reinstall that adds scopes reuses the same ``xoxb`` token value and never
    calls our callback, so the column keeps reporting the old set forever. On
    2026-08-25 Callie's column read 16 scopes while her live token carried 19,
    including the ``files:read`` the whole attachment path depends on.

    Returns ``None`` when the grant could not be READ (network failure, or Slack
    rejecting the token). ``None`` means "unknown", never "empty" — callers must
    not treat it as an empty scope set, or a transient outage would look like a
    total scope loss.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://slack.com/api/auth.test",
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError:
        return None
    if not response.json().get("ok"):
        return None
    raw = response.headers.get("x-oauth-scopes", "")
    return frozenset(s.strip() for s in raw.split(",") if s.strip())


async def assert_no_scope_regression(
    session: AsyncSession,
    *,
    provider: str,
    requested: list[str],
) -> None:
    """Refuse to start an OAuth flow that would STRIP a scope a live token holds.

    Slack replaces scopes on re-authorization rather than merging them, so an
    incomplete list here silently removes capability from a working token — the
    failure surfaces later as a ``missing_scope`` on some unrelated read path,
    with nothing pointing back to the reconnect that caused it. This turns that
    silent, delayed break into a loud refusal before the user ever reaches
    Slack's consent screen.

    It replaces a code comment instructing the next person to run the diff by
    hand. That comment existed on 2026-08-14 and the list still fell out of date.

    The two failure modes are reported SEPARATELY and never conflated:
      - a genuine regression names the agent and the exact scopes at risk;
      - an unreadable grant says so, and does not claim the list is unsafe.
    """
    integrations = await repo.list_active(session, provider=provider)
    requested_set = frozenset(requested)

    regressions: list[str] = []
    unreadable: list[str] = []

    from artemis.integrations.crypto import decrypt_credentials

    for integration in integrations:
        agent = str(getattr(integration, "agent_id", "") or f"id={integration.id}")
        if not integration.encrypted_credentials:
            continue
        try:
            creds = decrypt_credentials(bytes(integration.encrypted_credentials)) or {}
        except Exception:
            unreadable.append(f"{agent} (credentials could not be decrypted)")
            continue
        token = str(creds.get("access_token") or creds.get("bot_token") or creds.get("token") or "")
        if not token:
            continue

        live = await live_scopes_for_token(token)
        if live is None:
            unreadable.append(f"{agent} (Slack did not return a usable grant)")
            continue

        missing = live - requested_set
        if missing:
            regressions.append(f"{agent} would lose: {', '.join(sorted(missing))}")

    if regressions:
        raise HTTPException(
            status_code=500,
            detail=(
                "Refusing to start Slack OAuth: the requested scope list is not a "
                "superset of the live grant, so reconnecting would silently strip "
                "working capability. " + "; ".join(regressions) + ". Add these scopes "
                "to the list in artemis/routes/integrations.py before retrying."
            ),
        )

    if unreadable:
        logger.warning(
            "slack oauth preflight: could not READ the live grant for %s — "
            "this is a lookup failure, NOT evidence the scope list is unsafe. "
            "Proceeding; verify manually if a read path breaks after reconnect.",
            "; ".join(unreadable),
        )


# In-memory state store for non-Google OAuth (single-process; sufficient for V1).
_oauth_states: dict[str, str] = {}


class IntegrationOut(BaseModel):
    id: int
    provider: str
    workspace_id: str
    display_name: str | None
    connected_at: datetime
    status: str
    last_refresh_attempt_at: datetime | None = None

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


@router.get("", response_model=list[IntegrationOut], dependencies=[Depends(require_owner)])
async def list_integrations(
    provider: str | None = Query(default=None),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> list[Integration]:
    # list_for_ui returns active + needs_reauth so the Connectors modal can
    # surface needs_reauth rows with an amber reconnect CTA.  Service callers
    # that need only usable rows should call repo.list_active() directly.
    return await repo.list_for_ui(session, provider=provider)


@router.post("/{integration_id}/refresh", dependencies=[Depends(require_owner)])
async def refresh_integration(
    integration_id: int,
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, object]:
    """Trigger one proactive-refresh attempt for a single integration (J10e).

    Returns `{outcome, new_expires_at}`. `outcome` is the string value of the
    `RefreshOutcome` enum (`refreshed`, `still_valid`, `no_refresh_token`,
    `refresh_token_expired`, `transient_failure`, `no_refresher`).
    """
    from artemis.integrations.crypto import decrypt_credentials
    from artemis.integrations.token_refresh.base import RefreshOutcome
    from artemis.integrations.token_refresh.providers import REFRESHERS

    try:
        integration = await repo.get_by_id(session, integration_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    refresher = REFRESHERS.get(integration.provider)
    if refresher is None:
        return {"outcome": "no_refresher", "new_expires_at": None}

    try:
        creds = decrypt_credentials(bytes(integration.encrypted_credentials))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"could not decrypt creds: {exc}") from exc

    result = await refresher.refresh(creds)
    new_expires_at: float | None = None
    if result.outcome == RefreshOutcome.REFRESHED and result.new_creds is not None:
        raw = result.new_creds.get("expires_at")
        try:
            new_expires_at = float(raw) if raw is not None else None  # type: ignore[arg-type]
        except (TypeError, ValueError):
            new_expires_at = None
        await repo.persist_refreshed_credentials(
            session, integration_id=integration.id, new_creds=result.new_creds
        )
    elif result.outcome == RefreshOutcome.REFRESH_TOKEN_EXPIRED:
        await repo.mark_needs_reauth(session, integration.id)
    elif result.outcome == RefreshOutcome.TRANSIENT_FAILURE:
        await repo.mark_refresh_attempted(session, integration.id)
    await session.commit()

    return {"outcome": result.outcome.value, "new_expires_at": new_expires_at}


@router.delete("/{integration_id}", status_code=204, dependencies=[Depends(require_owner)])
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

    # !! This list must stay a SUPERSET of the union of every live BOT token's
    # grant, across every agent (artemis, callie, kai, ares) — they all install
    # through this one endpoint. Slack does not merge scopes across
    # authorizations: re-running this flow REPLACES the token with exactly what
    # is requested here.
    #
    # Verified live 2026-08-25 by reading the `x-oauth-scopes` response header
    # on `auth.test` for each stored bot token. Do NOT diff against
    # `integrations.scopes` — that column records what was granted when the row
    # was written and goes stale silently. Callie's row read 16 scopes while her
    # live token carried 19, because a Slack reinstall adds scopes to the SAME
    # xoxb token value and never calls our callback. `_assert_no_scope_regression`
    # below now enforces this against the live grant, not the column.
    #
    # search:read.* is here because Artemis's bot token carries it; files:read /
    # files:write / remote_files:read because Callie's does (the universal file
    # intake layer). Requesting the union is safe — a scope no agent needs is
    # simply approved and unused; omitting one an agent HAS silently breaks it.
    scopes = ",".join(
        [
            # write
            "chat:write",
            "chat:write.public",
            # read surfaces
            "channels:read",
            "channels:history",
            "groups:read",
            "groups:history",
            "im:read",
            "im:history",
            "im:write",
            "canvases:read",
            "canvases:write",
            # files — the agent-agnostic attachment intake layer
            "files:read",
            "files:write",
            "remote_files:read",
            # search (Artemis's bot token holds these)
            "search:read.public",
            "search:read.private",
            "search:read.im",
            "search:read.files",
            "search:read.users",
            # identity + reactions
            "users:read",
            "users:read.email",
            "reactions:read",
            "reactions:write",
            "app_mentions:read",
        ]
    )
    await assert_no_scope_regression(session, provider="slack", requested=scopes.split(","))
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
        agent_id="artemis",
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


def _gcal_redirect_uri() -> str:
    return os.environ.get(
        "GCAL_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/gcal/oauth/callback",
    )


def _gmail_redirect_uri() -> str:
    return os.environ.get(
        "GMAIL_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/gmail/oauth/callback",
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
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    try:
        cfg = await resolve_gcal_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"GCal credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc

    state = register_google_oauth_state(
        user_id=current_user.id,
        purpose="personal",
        source="gcal",
    )
    url = build_google_oauth_start_url(
        client_id=cfg.client_id,
        redirect_uri=_gcal_redirect_uri(),
        state=state,
        scopes=scopes_for_google_purpose("personal"),
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


@router.get("/salesforce/oauth/start")
async def salesforce_oauth_start_compat(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, str]:
    """Salesforce (SFDC-1) uses OAuth 2.0 Client Credentials -- server-to-server,
    no user redirect. Mirrors jira_oauth_start_compat above: verify the saved
    credentials against a real Salesforce token exchange, then create the
    integration row so the existing front-end Connect-button flow Just Works
    for Salesforce the same way it does for Jira.
    """
    from artemis.integrations import crypto
    from artemis.integrations.config_resolver import resolve_salesforce_config
    from artemis.integrations.salesforce.client import SalesforceAuthError, fetch_access_token

    try:
        cfg = await resolve_salesforce_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Salesforce credentials incomplete: {', '.join(exc.missing_fields)}",
        ) from exc

    try:
        # The token exchange itself IS the verification here -- unlike Jira's
        # basic auth, Salesforce's Client Credentials grant rejects bad
        # credentials at this exact step, so no separate API call is needed
        # to prove the creds work (contrast jira_oauth_start_compat above,
        # which must call get_overview because a token exchange has no
        # jira-side equivalent to fail on). The token itself is not persisted
        # -- see resolve_salesforce_config's docstring on why a fresh one is
        # fetched per suppression check instead of cached.
        await fetch_access_token(
            login_url=cfg.login_url, client_id=cfg.client_id, client_secret=cfg.client_secret
        )
    except SalesforceAuthError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Salesforce rejected your credentials ({exc}). Double-check the Client ID, "
                "Client Secret, and Login URL, and confirm with Neil that the Connected App's "
                "OAuth policy permits the Client Credentials flow for the integration user."
            ),
        ) from exc

    creds_blob = crypto.encrypt_credentials(
        {
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "login_url": cfg.login_url,
        }
    )
    await repo.upsert_integration(
        session,
        provider="salesforce",
        workspace_id=cfg.login_url,
        display_name=cfg.login_url.replace("https://", "").rstrip("/"),
        encrypted_credentials=creds_blob,
        scopes=None,
    )
    await session.commit()

    return {"url": "/?salesforce_connected=1"}


@router.get("/gcal/oauth/callback")
async def gcal_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> RedirectResponse:
    await complete_google_oauth(
        session=session,
        current_user_id=current_user.id,
        code=code,
        state=state,
        redirect_uri=_gcal_redirect_uri(),
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


@router.get("/gmail/oauth/start")
async def gmail_oauth_start(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    config = await resolve_google_oauth_client_config(session)
    state = register_google_oauth_state(
        user_id=current_user.id,
        purpose="personal",
        source="gmail",
    )
    url = build_google_oauth_start_url(
        client_id=config.client_id,
        redirect_uri=_gmail_redirect_uri(),
        state=state,
        scopes=scopes_for_google_purpose("personal"),
    )
    return {"url": url}


@router.get("/gmail/oauth/callback")
async def gmail_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> RedirectResponse:
    await complete_google_oauth(
        session=session,
        current_user_id=current_user.id,
        code=code,
        state=state,
        redirect_uri=_gmail_redirect_uri(),
    )
    await session.commit()
    return RedirectResponse(url="/?gmail_connected=1", status_code=302)


# ── Slack user-token OAuth (radar + agency-writes) ────────────────────────────
#
# A SECOND Slack OAuth flow that requests a *user* token (not bot).
# Stored as provider="slack_user" in the integrations table — never clobbers
# the bot-token row.  Scopes: search:read, users:read, chat:write (pre-requested
# for the agency-writes lane that will use the same token later).


def _slack_user_redirect_uri() -> str:
    return os.environ.get(
        "SLACK_USER_REDIRECT_URI",
        "https://app.artemisos.me/api/integrations/slack-user/oauth/callback",
    )


@router.get("/slack-user/oauth/start")
async def slack_user_oauth_start(
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> dict[str, str]:
    """Return a Slack OAuth URL that requests a user token (search:read etc.).

    Jon visits this URL in a browser to re-auth the radar user token.
    The resulting token is stored as provider='slack_user', separate from the
    bot token so the existing bot flows are unaffected.
    """
    try:
        cfg = await resolve_slack_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Slack credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = "slack_user_pending"

    # user_scope requests a *user* token (vs scope for a bot token). Slack
    # deprecated the umbrella ``search:read`` in favour of granular scopes, and
    # search.messages (how the radar finds Jon's mentions) needs a USER token —
    # so request the granular search scopes across public/private/DM surfaces.
    # chat:write is pre-requested here for the agency-writes Slack-send lane.
    #
    # mpim:history/mpim:read are NOT implied by im:history — verified 2026-08-14:
    # conversations.history and conversations.info on an mpdm channel both return
    # missing_scope on a token holding im:history alone. search.messages still
    # surfaces mpdm message *text* (via search:read.im), which is why the gap is
    # easy to miss: the content appears, the conversation cannot be read. Group
    # DMs are a large surface here (69 of the 140 conversations one departed
    # colleague appeared in), so they are requested explicitly.
    #
    # !! This list must stay a SUPERSET of every scope the live token already
    # holds. Slack does not merge scopes across authorizations — re-running this
    # flow REPLACES the stored token with exactly what is requested here. On
    # 2026-08-14 this list held only the five search/user/chat scopes while the
    # live token carried 21, so a single reconnect would have silently dropped
    # channels:history, groups:history and im:history and broken every read path
    # (DM history, channel crawls, the awaiting-reply radar) with no error beyond
    # a later missing_scope. Before editing, diff against the live grant:
    #   select scopes from integrations where provider='slack_user';
    user_scopes = ",".join(
        [
            # read surfaces — history is what every crawl and the radar depend on
            "channels:history",
            "groups:history",
            "im:history",
            "mpim:history",
            "channels:read",
            "groups:read",
            "im:read",
            "mpim:read",
            "canvases:read",
            # search — Slack deprecated the umbrella search:read for granular scopes
            "search:read.public",
            "search:read.private",
            "search:read.im",
            "search:read.files",
            "search:read.users",
            # The deprecated umbrella search:read is requested DESPITE the
            # granular scopes above because the live user token still carries it
            # (verified 2026-08-25 via the x-oauth-scopes header on auth.test).
            # The superset rule outranks the deprecation: omitting it strips a
            # scope the live token holds. If Slack ever rejects the authorize URL
            # with invalid_scope, drop this line — that failure is loud and
            # happens BEFORE any token is replaced, which is the safe direction.
            # Slack grants `identify` implicitly and it cannot be requested, so
            # it is deliberately absent here despite being on the live token.
            "search:read",
            # identity
            "users:read",
            "users:read.email",
            "users.profile:read",
            # files — the agent-agnostic attachment intake layer
            "files:read",
            "files:write",
            "remote_files:read",
            # write — the agency-writes Slack-send lane
            "chat:write",
            "channels:write",
            "groups:write",
            "im:write",
            "canvases:write",
        ]
    )
    await assert_no_scope_regression(
        session, provider="slack_user", requested=user_scopes.split(",")
    )
    url = (
        f"https://slack.com/oauth/v2/authorize"
        f"?client_id={cfg.client_id}"
        f"&user_scope={user_scopes}"
        f"&redirect_uri={_slack_user_redirect_uri()}"
        f"&state={state}"
    )
    return {"url": url}


@router.get("/slack-user/oauth/callback")
async def slack_user_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    session: AsyncSession = Depends(db.get_session),  # noqa: B008
) -> RedirectResponse:
    """Exchange the user-token OAuth code and store as provider='slack_user'."""
    if state not in _oauth_states or _oauth_states[state] != "slack_user_pending":
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    del _oauth_states[state]

    try:
        cfg = await resolve_slack_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Slack credentials incomplete: {', '.join(exc.missing_fields)} not configured.",
        ) from exc

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "code": code,
                "client_id": cfg.client_id,
                "client_secret": cfg.client_secret,
                "redirect_uri": _slack_user_redirect_uri(),
            },
        )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=f"Slack OAuth error: {data.get('error', 'unknown')}",
        )

    # oauth.v2.access with user_scope returns the user token under authed_user.
    authed_user: dict[str, object] = data.get("authed_user") or {}
    user_token = str(authed_user.get("access_token") or "")
    if not user_token:
        raise HTTPException(status_code=400, detail="Slack did not return a user access token.")

    user_scopes_granted = str(authed_user.get("scope") or "").split(",")
    team_id = str((data.get("team") or {}).get("id") or "")
    team_name = str((data.get("team") or {}).get("name") or team_id)

    from artemis.integrations.crypto import encrypt_credentials

    encrypted = encrypt_credentials({"access_token": user_token, "token_type": "user"})

    await repo.upsert_integration(
        session,
        provider="slack_user",
        workspace_id=team_id,
        agent_id="artemis",
        encrypted_credentials=encrypted,
        display_name=f"{team_name} (user token)",
        bot_user_id=None,
        scopes=user_scopes_granted,
    )
    await session.commit()

    return RedirectResponse(url="/?slack_user_connected=1", status_code=302)


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


@router.get(
    "/providers/{provider}/config",
    response_model=ProviderConfigOut,
    dependencies=[Depends(require_owner)],
)
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


@router.post(
    "/providers/{provider}/config",
    response_model=ProviderConfigOut,
    dependencies=[Depends(require_owner)],
)
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


@router.delete(
    "/providers/{provider}/config", status_code=204, dependencies=[Depends(require_owner)]
)
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
    # Always include an explicit port. When accessed via a tunnel host (no
    # port in Host), default to :8000 — the local uvicorn port — so the
    # OAuth callback lands on the running server, not port 80.
    port = f":{port_match.group(1)}" if port_match else ":8000"
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
