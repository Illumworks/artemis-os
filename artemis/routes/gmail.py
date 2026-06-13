"""Gmail read routes backed by the personal Google credential."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
from artemis.google_docs.models import GoogleCredential
from artemis.google_docs.repository import get_google_credential
from artemis.google_integration import google_has_any_scope, resolve_google_oauth_client_config
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.integrations.gmail.client import GmailAPIError, GmailClient
from artemis.marketing.routes._auth import require_token

router = APIRouter(
    prefix="/api/gmail",
    tags=["gmail"],
    dependencies=[Depends(require_token)],
)


def _gmail_http_error(status_code: int, error: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, "code": code})


async def _require_personal_gmail_credential(
    session: AsyncSession,
    *,
    current_user: User,
) -> GoogleCredential:
    credential = await get_google_credential(session, user_id=current_user.id, purpose="personal")
    if credential is None:
        raise _gmail_http_error(409, "Connect Google first", "google_not_connected")
    if not google_has_any_scope(
        credential.scope,
        "https://www.googleapis.com/auth/gmail.readonly",
    ):
        raise _gmail_http_error(
            409,
            "Reconnect Google to grant Gmail read access",
            "gmail_reconnect_required",
        )
    return credential


async def _valid_access_token(
    session: AsyncSession,
    *,
    credential: GoogleCredential,
) -> str:
    now = datetime.now(UTC)
    if credential.expiry > now + timedelta(seconds=60):
        return credential.access_token
    if not credential.refresh_token:
        raise _gmail_http_error(
            409, "Reconnect Google to refresh access", "google_reconnect_required"
        )

    config = await resolve_google_oauth_client_config(session)
    try:
        refreshed = await refresh_access_token(
            refresh_token=credential.refresh_token,
            client_id=config.client_id,
            client_secret=config.client_secret,
        )
    except GoogleReauthRequiredError as exc:
        raise _gmail_http_error(
            409,
            "Reconnect Google to refresh access",
            "google_reconnect_required",
        ) from exc
    except httpx.HTTPError as exc:
        raise _gmail_http_error(
            502, f"Gmail token refresh failed: {exc}", "gmail_refresh_failed"
        ) from exc

    credential.access_token = refreshed.access_token
    credential.refresh_token = refreshed.refresh_token
    credential.expiry = refreshed.expiry
    if refreshed.scope:
        credential.scope = refreshed.scope
    credential.updated_at = now
    return credential.access_token


async def _gmail_client(
    session: AsyncSession,
    *,
    current_user: User,
) -> GmailClient:
    credential = await _require_personal_gmail_credential(session, current_user=current_user)
    access_token = await _valid_access_token(session, credential=credential)
    config = await resolve_google_oauth_client_config(session)
    return GmailClient(
        access_token=access_token,
        refresh_token=credential.refresh_token or "",
        client_id=config.client_id,
        client_secret=config.client_secret,
    )


@router.get("/messages")
async def list_gmail_messages(
    limit: int = Query(default=10, ge=1, le=50),
    query: str | None = Query(default=None, alias="q"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    client = await _gmail_client(session, current_user=current_user)
    try:
        messages = await client.list_recent_messages(max_results=limit, query=query)
    except (httpx.HTTPError, GmailAPIError) as exc:
        raise _gmail_http_error(502, f"Gmail read failed: {exc}", "gmail_read_failed") from exc
    await session.commit()
    return {"messages": messages, "count": len(messages)}


@router.get("/threads/{thread_id}")
async def get_gmail_thread(
    thread_id: str = Path(..., min_length=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    client = await _gmail_client(session, current_user=current_user)
    try:
        thread = await client.get_thread(thread_id)
    except (httpx.HTTPError, GmailAPIError) as exc:
        raise _gmail_http_error(
            502, f"Gmail thread fetch failed: {exc}", "gmail_read_failed"
        ) from exc
    await session.commit()
    return thread
