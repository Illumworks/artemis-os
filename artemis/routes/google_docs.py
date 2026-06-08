"""Per-user Google Docs connect/import/export routes."""

from __future__ import annotations

import logging
import secrets
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.config import settings
from artemis.db import get_session
from artemis.google_docs.client import (
    GoogleDocsError,
    GoogleReauthRequiredError,
    InvalidGoogleDocumentReferenceError,
    build_google_oauth_start_url,
    compact_preview_text,
    exchange_code_for_tokens,
    export_google_document,
    extract_document_id,
    import_google_document,
    refresh_access_token,
    revoke_token,
)
from artemis.google_docs.models import GoogleCredential
from artemis.google_docs.repository import (
    delete_google_credential,
    get_google_credential,
    upsert_google_credential,
)
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.marketing.models import CampaignDeliverable
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import not_found
from artemis.marketing.writing_studio.compose_engine import _latest_draft_content

router = APIRouter(tags=["google"], dependencies=[Depends(require_token)])

_oauth_states: dict[str, int] = {}


def _http_error(status_code: int, error: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, "code": code})


def _google_config_or_503() -> tuple[str, str, str]:
    if not settings.google_client_id or not settings.google_client_secret:
        raise _http_error(
            503,
            "Google credentials are not configured",
            "google_not_configured",
        )
    return (
        settings.google_client_id,
        settings.google_client_secret,
        settings.google_redirect_uri,
    )


async def _require_google_credential(
    session: AsyncSession,
    *,
    current_user: User,
) -> GoogleCredential:
    credential = await get_google_credential(session, user_id=current_user.id)
    if credential is None:
        raise _http_error(409, "Connect Google first", "google_not_connected")
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
        raise _http_error(409, "Reconnect Google to refresh access", "google_reconnect_required")

    client_id, client_secret, _redirect_uri = _google_config_or_503()
    try:
        refreshed = await refresh_access_token(
            refresh_token=credential.refresh_token,
            client_id=client_id,
            client_secret=client_secret,
        )
    except GoogleReauthRequiredError as exc:
        raise _http_error(
            409,
            "Reconnect Google to refresh access",
            "google_reconnect_required",
        ) from exc
    except httpx.HTTPError as exc:
        raise _http_error(
            502, f"Google token refresh failed: {exc}", "google_refresh_failed"
        ) from exc

    credential.access_token = refreshed.access_token
    credential.refresh_token = refreshed.refresh_token
    credential.expiry = refreshed.expiry
    if refreshed.scope:
        credential.scope = refreshed.scope
    credential.updated_at = now
    return credential.access_token


def _draft_google_doc_metadata(draft: CampaignDeliverable) -> dict[str, Any]:
    meta = draft.deliverable_metadata if isinstance(draft.deliverable_metadata, dict) else {}
    google_doc = meta.get("googleDoc")
    return google_doc if isinstance(google_doc, dict) else {}


def _draft_title(draft: CampaignDeliverable) -> str:
    meta = draft.deliverable_metadata if isinstance(draft.deliverable_metadata, dict) else {}
    title = meta.get("title") or meta.get("externalTitle")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return f"Draft {draft.id}"


@router.get("/api/google/oauth/start")
async def google_oauth_start(current_user: User = Depends(get_current_user)) -> RedirectResponse:  # noqa: B008
    client_id, _client_secret, redirect_uri = _google_config_or_503()
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = current_user.id
    url = build_google_oauth_start_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/api/google/oauth/callback")
async def google_oauth_callback(
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> RedirectResponse:
    expected_user_id = _oauth_states.pop(state, None)
    if expected_user_id is None or expected_user_id != current_user.id:
        raise _http_error(400, "Invalid or expired OAuth state", "invalid_google_oauth_state")

    client_id, client_secret, redirect_uri = _google_config_or_503()
    existing = await get_google_credential(session, user_id=current_user.id)
    try:
        tokens = await exchange_code_for_tokens(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
        )
    except httpx.HTTPError as exc:
        raise _http_error(
            502, f"Google OAuth exchange failed: {exc}", "google_oauth_failed"
        ) from exc

    refresh_token = tokens.refresh_token or (existing.refresh_token if existing else None)
    await upsert_google_credential(
        session,
        user_id=current_user.id,
        access_token=tokens.access_token,
        refresh_token=refresh_token,
        expiry=tokens.expiry,
        scope=tokens.scope,
        connected_email=tokens.connected_email,
    )
    await session.commit()
    return RedirectResponse(url="/?google_connected=1", status_code=302)


@router.get("/api/google/status")
async def google_status(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    credential = await get_google_credential(session, user_id=current_user.id)
    if credential is None:
        return {"connected": False}
    response: dict[str, Any] = {"connected": True}
    if credential.connected_email:
        response["email"] = credential.connected_email
    return response


@router.post("/api/google/disconnect")
async def google_disconnect(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    credential = await get_google_credential(session, user_id=current_user.id)
    if credential is not None:
        with suppress(httpx.HTTPError):
            await revoke_token(token=credential.access_token)
        await delete_google_credential(session, user_id=current_user.id)
        await session.commit()
    return {"ok": True, "connected": False}


@router.post("/api/writing-studio/drafts/{draft_id}/google-doc/import")
async def import_draft_from_google_doc(
    body: dict[str, Any],
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    draft = await session.get(CampaignDeliverable, draft_id)
    if draft is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    credential = await _require_google_credential(session, current_user=current_user)
    existing_google_doc = _draft_google_doc_metadata(draft)
    requested_doc = (
        body.get("docUrl")
        or body.get("documentId")
        or existing_google_doc.get("url")
        or existing_google_doc.get("documentId")
    )
    try:
        document_id = extract_document_id(str(requested_doc) if requested_doc is not None else None)
    except InvalidGoogleDocumentReferenceError as exc:
        raise _http_error(400, str(exc), "invalid_google_doc_reference") from exc

    access_token = await _valid_access_token(session, credential=credential)
    try:
        imported = await import_google_document(access_token=access_token, document_id=document_id)
    except httpx.HTTPError as exc:
        raise _http_error(502, f"Google Docs import failed: {exc}", "google_import_failed") from exc
    except GoogleDocsError as exc:
        raise _http_error(400, str(exc), "google_import_failed") from exc

    imported_at = datetime.now(UTC).isoformat()
    preview_text = compact_preview_text(imported.content)
    meta = dict(draft.deliverable_metadata or {})
    previous_google_doc = _draft_google_doc_metadata(draft)
    meta["googleDoc"] = {
        **previous_google_doc,
        "documentId": imported.document_id,
        "title": imported.title,
        "url": imported.url,
        "importedAt": imported_at,
        "previewText": preview_text,
    }
    meta["live_content"] = imported.content
    meta["live_content_updated_at"] = imported_at
    draft.deliverable_metadata = meta
    draft.updated_at = datetime.now(UTC)
    await session.commit()

    return {
        "ok": True,
        "draftId": draft.id,
        "importedContent": imported.content,
        "linkedDocId": imported.document_id,
        "googleDoc": meta["googleDoc"],
    }


@router.post("/api/writing-studio/drafts/{draft_id}/google-doc/export")
async def export_draft_to_google_doc(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    draft = await session.get(CampaignDeliverable, draft_id)
    if draft is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    credential = await _require_google_credential(session, current_user=current_user)
    access_token = await _valid_access_token(session, credential=credential)

    existing_google_doc = _draft_google_doc_metadata(draft)
    current_content = _latest_draft_content(draft)
    try:
        exported = await export_google_document(
            access_token=access_token,
            title=_draft_title(draft),
            content=current_content,
            document_id=existing_google_doc.get("documentId")
            if isinstance(existing_google_doc.get("documentId"), str)
            else None,
        )
    except httpx.HTTPError as exc:
        _detail = str(exc)
        _resp = getattr(exc, "response", None)
        if _resp is not None:
            with suppress(Exception):
                _detail = f"HTTP {_resp.status_code}: {_resp.text[:500]}"
        logging.getLogger(__name__).warning("Google Docs export failed — %s", _detail)
        raise _http_error(502, "Google Docs export failed", "google_export_failed") from exc

    exported_at = datetime.now(UTC).isoformat()
    meta = dict(draft.deliverable_metadata or {})
    meta["googleDoc"] = {
        **existing_google_doc,
        "documentId": exported.document_id,
        "title": exported.title,
        "url": exported.url,
        "lastExportedAt": exported_at,
        "previewText": compact_preview_text(current_content),
    }
    draft.deliverable_metadata = meta
    draft.updated_at = datetime.now(UTC)
    await session.commit()

    return {
        "ok": True,
        "draftId": draft.id,
        "linkedDocId": exported.document_id,
        "docUrl": exported.url,
        "created": exported.created,
        "googleDoc": meta["googleDoc"],
    }
