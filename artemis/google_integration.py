"""Shared Google OAuth/account helpers across Docs, Calendar, and Gmail."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.google_docs.client import GoogleTokenExchangeError, exchange_code_for_tokens

logger = logging.getLogger(__name__)
from artemis.google_docs.models import GoogleCredential
from artemis.google_docs.repository import get_google_credential, upsert_google_credential
from artemis.integrations import repository as integrations_repo
from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_gcal_config
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration

GooglePurpose = Literal["personal", "marketing"]
GoogleOAuthSource = Literal["google", "gcal", "gmail"]

GOOGLE_MARKETING_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/documents",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
)

GOOGLE_PERSONAL_SCOPES: tuple[str, ...] = (
    *GOOGLE_MARKETING_SCOPES,
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    # Full drive (not drive.file) is required for files the app did not create:
    # Drive returns 404 on an externally-owned doc under drive.file, which blocks
    # both the export endpoint and comments.create (the @mention path). See the
    # access note in artemis/enablement/sync.py, which hit the same 403.
    "https://www.googleapis.com/auth/drive",
)

_KNOWN_PURPOSES = frozenset({"personal", "marketing"})


@dataclass(frozen=True)
class GoogleOAuthState:
    user_id: int
    purpose: GooglePurpose
    source: GoogleOAuthSource


@dataclass(frozen=True)
class GoogleOAuthClientConfig:
    client_id: str
    client_secret: str


_oauth_states: dict[str, GoogleOAuthState] = {}


def clear_google_oauth_states() -> None:
    _oauth_states.clear()


def register_google_oauth_state(
    *,
    user_id: int,
    purpose: GooglePurpose,
    source: GoogleOAuthSource,
) -> str:
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = GoogleOAuthState(user_id=user_id, purpose=purpose, source=source)
    return state


def pop_google_oauth_state(state: str) -> GoogleOAuthState | None:
    return _oauth_states.pop(state, None)


def normalize_google_purpose(raw: str | None) -> GooglePurpose:
    purpose = (raw or "personal").strip().lower()
    if purpose not in _KNOWN_PURPOSES:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unsupported Google purpose: {raw!r}",
                "code": "invalid_google_purpose",
            },
        )
    return purpose  # type: ignore[return-value]


def scopes_for_google_purpose(purpose: GooglePurpose) -> tuple[str, ...]:
    if purpose == "marketing":
        return GOOGLE_MARKETING_SCOPES
    return GOOGLE_PERSONAL_SCOPES


def google_scope_set(scope_value: str | None) -> set[str]:
    return {part.strip() for part in (scope_value or "").split() if part.strip()}


def google_has_any_scope(scope_value: str | None, *expected: str) -> bool:
    granted = google_scope_set(scope_value)
    return any(scope in granted for scope in expected)


def google_status_payload(credential: GoogleCredential | None) -> dict[str, object]:
    if credential is None:
        return {
            "connected": False,
            "purpose": "personal",
            "hasDriveScope": False,
            "docsImportReady": False,
            "docsExportReady": False,
            "hasCalendarScope": False,
            "hasGmailReadScope": False,
            "hasGmailSendScope": False,
        }

    return {
        "connected": True,
        "purpose": credential.purpose,
        "email": credential.connected_email,
        "hasDriveScope": google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive",
        ),
        "docsImportReady": google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/documents",
        ),
        "docsExportReady": google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/documents",
        )
        and google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive",
        ),
        "hasCalendarScope": google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.events",
        ),
        "hasGmailReadScope": google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/gmail.readonly",
        ),
        "hasGmailSendScope": google_has_any_scope(
            credential.scope,
            "https://www.googleapis.com/auth/gmail.send",
        ),
    }


async def resolve_google_oauth_client_config(
    session: AsyncSession,
) -> GoogleOAuthClientConfig:
    if settings.google_client_id and settings.google_client_secret:
        return GoogleOAuthClientConfig(
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
        )
    try:
        gcal_cfg = await resolve_gcal_config(session)
    except MissingProviderConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Google OAuth credentials are not configured",
                "code": "google_not_configured",
                "details": {"missing_fields": exc.missing_fields},
            },
        ) from exc
    return GoogleOAuthClientConfig(
        client_id=gcal_cfg.client_id, client_secret=gcal_cfg.client_secret
    )


async def sync_personal_google_integrations(
    session: AsyncSession,
    *,
    credential: GoogleCredential,
    client_id: str,
    client_secret: str,
    expires_at: float | None = None,
) -> None:
    """Mirror a personal Google credential into the gcal Integration row.

    Rules:
    - If the credential has calendar scope → upsert the gcal integration to
      ``active`` with the calendar scopes (self-healing: a previously ``revoked``
      row is flipped back to ``active``).
    - If the credential does NOT have calendar scope → do nothing.  Leave any
      existing integration row exactly as-is; never revoke here.

    Revocation belongs exclusively to the explicit disconnect path
    (``revoke_personal_google_integrations`` / ``google_disconnect``).
    """
    if not credential.connected_email:
        return

    has_calendar = google_has_any_scope(
        credential.scope,
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
    )
    if not has_calendar:
        # No calendar scope on this credential — leave existing integration
        # untouched.  A future consent that includes calendar will heal it.
        return

    creds_dict: dict[str, object] = {
        "access_token": credential.access_token,
        "refresh_token": credential.refresh_token or "",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    # Include expires_at so the proactive token-refresh scheduler can pick up
    # this row.  Fall back to a 1-hour window if the caller didn't provide it.
    creds_dict["expires_at"] = expires_at if expires_at is not None else time.time() + 3600
    encrypted = encrypt_credentials(creds_dict)
    await integrations_repo.upsert_integration(
        session,
        provider="gcal",
        workspace_id=credential.connected_email,
        encrypted_credentials=encrypted,
        display_name=credential.connected_email,
        scopes=[
            scope
            for scope in (
                "https://www.googleapis.com/auth/calendar",
                "https://www.googleapis.com/auth/calendar.events",
            )
            if google_has_any_scope(credential.scope, scope)
        ],
    )


async def revoke_personal_google_integrations(
    session: AsyncSession,
    *,
    connected_email: str | None,
) -> None:
    if not connected_email:
        return
    await session.execute(
        update(Integration)
        .where(
            Integration.provider == "gcal",
            Integration.workspace_id == connected_email,
            Integration.agent_id == "default",
        )
        .values(status="revoked")
    )


async def complete_google_oauth(
    *,
    session: AsyncSession,
    current_user_id: int,
    code: str,
    state: str,
    redirect_uri: str,
) -> GoogleOAuthState:
    expected_state = pop_google_oauth_state(state)
    if expected_state is None or expected_state.user_id != current_user_id:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "Invalid or expired OAuth state",
                "code": "invalid_google_oauth_state",
            },
        )

    config = await resolve_google_oauth_client_config(session)
    existing = await get_google_credential(
        session,
        user_id=current_user_id,
        purpose=expected_state.purpose,
    )
    try:
        tokens = await exchange_code_for_tokens(
            code=code,
            client_id=config.client_id,
            client_secret=config.client_secret,
            redirect_uri=redirect_uri,
        )
    except GoogleTokenExchangeError as exc:
        # Google was reachable but rejected the exchange (e.g. invalid_grant,
        # redirect_uri_mismatch, invalid_client).  This is a 4xx/config issue,
        # not a gateway error — surface Google's real reason to the caller.
        logger.error(
            "Google token exchange rejected: status=%d error=%r description=%r",
            exc.status,
            exc.error_code,
            exc.error_description,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "google_rejected_token_exchange",
                "google_error": exc.error_code,
                "google_error_description": exc.error_description,
                "google_status": exc.status,
            },
        ) from exc
    except httpx.HTTPError as exc:
        # Google was unreachable (connect error / timeout) — genuine gateway failure.
        logger.error("Google OAuth exchange network error: %s", exc)
        raise HTTPException(
            status_code=502,
            detail={
                "error": f"Google OAuth exchange failed: {exc}",
                "code": "google_oauth_failed",
            },
        ) from exc

    refresh_token = tokens.refresh_token or (existing.refresh_token if existing else None)
    stored = await upsert_google_credential(
        session,
        user_id=current_user_id,
        purpose=expected_state.purpose,
        access_token=tokens.access_token,
        refresh_token=refresh_token,
        expiry=tokens.expiry,
        scope=tokens.scope,
        connected_email=tokens.connected_email,
    )
    if expected_state.purpose == "personal":
        # tokens.expiry is a UTC-aware datetime from the token exchange.
        token_expires_at: float | None = None
        if tokens.expiry is not None:
            try:
                token_expires_at = tokens.expiry.timestamp()
            except Exception:
                token_expires_at = None
        await sync_personal_google_integrations(
            session,
            credential=stored,
            client_id=config.client_id,
            client_secret=config.client_secret,
            expires_at=token_expires_at,
        )
    return expected_state
