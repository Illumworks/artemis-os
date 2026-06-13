"""Per-user Google Docs connect/import/export routes."""

from __future__ import annotations

import logging
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
    list_google_credentials,
)
from artemis.google_integration import (
    GooglePurpose,
    complete_google_oauth,
    google_status_payload,
    normalize_google_purpose,
    register_google_oauth_state,
    resolve_google_oauth_client_config,
    revoke_personal_google_integrations,
    scopes_for_google_purpose,
)
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.marketing.models import CampaignDeliverable
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import not_found
from artemis.marketing.writing_studio.compose_engine import _latest_draft_content

router = APIRouter(tags=["google"], dependencies=[Depends(require_token)])


def _http_error(status_code: int, error: str, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"error": error, "code": code})


async def _google_config_or_503(session: AsyncSession) -> tuple[str, str, str]:
    config = await resolve_google_oauth_client_config(session)
    return (config.client_id, config.client_secret, settings.google_redirect_uri)


async def _require_google_credential(
    session: AsyncSession,
    *,
    current_user: User,
    purpose: GooglePurpose = "personal",
) -> GoogleCredential:
    credential = await get_google_credential(session, user_id=current_user.id, purpose=purpose)
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

    client_id, client_secret, _redirect_uri = await _google_config_or_503(session)
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
async def google_oauth_start(
    purpose: str = Query(default="personal"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> RedirectResponse:
    resolved_purpose = normalize_google_purpose(purpose)
    client_id, _client_secret, redirect_uri = await _google_config_or_503(session)
    state = register_google_oauth_state(
        user_id=current_user.id,
        purpose=resolved_purpose,
        source="google",
    )
    url = build_google_oauth_start_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scopes=scopes_for_google_purpose(resolved_purpose),
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/api/google/oauth/callback")
async def google_oauth_callback(
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> RedirectResponse:
    expected_state = await complete_google_oauth(
        session=session,
        current_user_id=current_user.id,
        code=code,
        state=state,
        redirect_uri=settings.google_redirect_uri,
    )
    await session.commit()
    success_param = {
        "google": "google_connected=1",
        "gcal": "gcal_connected=1",
        "gmail": "gmail_connected=1",
    }[expected_state.source]
    return RedirectResponse(url=f"/?{success_param}", status_code=302)


@router.get("/api/google/status")
async def google_status(
    purpose: str = Query(default="personal"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    resolved_purpose = normalize_google_purpose(purpose)
    credential = await get_google_credential(
        session,
        user_id=current_user.id,
        purpose=resolved_purpose,
    )
    response = google_status_payload(credential)
    response["purpose"] = resolved_purpose
    return response


@router.get("/api/google/accounts")
async def google_accounts_status(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    rows = await list_google_credentials(session, user_id=current_user.id)
    accounts: list[dict[str, object]] = []
    for row in rows:
        payload = google_status_payload(row)
        payload["purpose"] = row.purpose
        accounts.append(payload)
    return {"accounts": accounts}


@router.get("/api/google/overview")
async def google_overview(
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    credential = await get_google_credential(
        session,
        user_id=current_user.id,
        purpose="marketing",
    )
    response = google_status_payload(credential)
    response["purpose"] = "marketing"
    return response


@router.post("/api/google/disconnect")
async def google_disconnect(
    purpose: str = Query(default="personal"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    resolved_purpose = normalize_google_purpose(purpose)
    credential = await get_google_credential(
        session,
        user_id=current_user.id,
        purpose=resolved_purpose,
    )
    if credential is not None:
        with suppress(httpx.HTTPError):
            await revoke_token(token=credential.access_token)
        await delete_google_credential(
            session,
            user_id=current_user.id,
            purpose=resolved_purpose,
        )
        if resolved_purpose == "personal":
            await revoke_personal_google_integrations(
                session,
                connected_email=credential.connected_email,
            )
        await session.commit()
    return {"ok": True, "connected": False, "purpose": resolved_purpose}


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

    credential = await _require_google_credential(
        session,
        current_user=current_user,
        purpose="marketing",
    )
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

    credential = await _require_google_credential(
        session,
        current_user=current_user,
        purpose="marketing",
    )
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
