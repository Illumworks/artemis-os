"""Minimal Google Docs/Drive REST client for Writing Studio import/export."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus

import httpx

from artemis.config import settings

logger = logging.getLogger(__name__)

_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
_GOOGLE_DOCS_BASE_URL = "https://docs.googleapis.com/v1"
_GOOGLE_DRIVE_BASE_URL = "https://www.googleapis.com/drive/v3"

_DOCUMENT_URL_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")
_PLAIN_DOCUMENT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{10,}$")


class GoogleDocsError(Exception):
    """Base error for Google Docs integration failures."""


class InvalidGoogleDocumentReferenceError(GoogleDocsError):
    """Raised when the caller provides neither a Google Doc URL nor id."""


class GoogleReauthRequiredError(GoogleDocsError):
    """Raised when the stored refresh token can no longer mint access tokens."""


class GoogleTokenExchangeError(GoogleDocsError):
    """Raised when Google rejects the token exchange with a 4xx response.

    This is a *Google rejection* (Google was reachable but said no), not a
    network/gateway error.  Carries the structured error fields from Google's
    ``{"error": ..., "error_description": ...}`` JSON body so callers can
    surface the real reason to the user rather than an opaque 502.
    """

    def __init__(
        self,
        *,
        status: int,
        error_code: str | None,
        error_description: str | None,
        body: str,
    ) -> None:
        super().__init__(
            f"Google token exchange rejected: HTTP {status} "
            f"error={error_code!r} description={error_description!r}"
        )
        self.status = status
        self.error_code = error_code
        self.error_description = error_description
        self.body = body


@dataclass(frozen=True)
class GoogleTokenBundle:
    access_token: str
    refresh_token: str | None
    expiry: datetime
    scope: str | None
    connected_email: str | None


@dataclass(frozen=True)
class GoogleDocumentRecord:
    document_id: str
    title: str
    url: str
    content: str
    created: bool = False


def _make_http_client(*, timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout)


def build_google_oauth_start_url(
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    scopes: tuple[str, ...],
) -> str:
    scope = quote_plus(" ".join(scopes))
    return (
        f"{_GOOGLE_AUTH_URL}"
        f"?client_id={quote_plus(client_id)}"
        f"&redirect_uri={quote_plus(redirect_uri)}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
        # Incremental authorization: the new token carries the scopes this user
        # ALREADY granted, plus whatever is requested above. Without it Google
        # issues a token holding exactly `scopes` and nothing else, so shipping a
        # reconnect link with an incomplete list silently strips live capability
        # -- the same failure Slack's OAuth has, where the only defence is a
        # hand-maintained superset list (see `assert_no_scope_regression` in
        # artemis/routes/integrations.py). Google supports fixing it at the
        # source; this makes a reconnect purely additive and removes the whole
        # bug class here rather than guarding against it case by case.
        f"&include_granted_scopes=true"
        f"&state={quote_plus(state)}"
    )


def extract_document_id(doc_url_or_id: str | None) -> str:
    candidate = (doc_url_or_id or "").strip()
    if not candidate:
        raise InvalidGoogleDocumentReferenceError("docUrl or documentId is required")

    match = _DOCUMENT_URL_RE.search(candidate)
    if match:
        return match.group(1)
    if _PLAIN_DOCUMENT_ID_RE.fullmatch(candidate):
        return candidate
    raise InvalidGoogleDocumentReferenceError("docUrl must be a Google Docs URL or document id")


async def exchange_code_for_tokens(
    *,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> GoogleTokenBundle:
    async with _make_http_client(timeout=20.0) as http:
        token_resp = await http.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if token_resp.status_code >= 400:
            raw_body = token_resp.text
            error_code: str | None = None
            error_description: str | None = None
            try:
                err_json = token_resp.json()
                error_code = str(err_json.get("error") or "") or None
                error_description = str(err_json.get("error_description") or "") or None
            except Exception:
                pass
            logger.error(
                "Google token exchange rejected: status=%d error=%r description=%r body=%r",
                token_resp.status_code,
                error_code,
                error_description,
                raw_body[:500],
            )
            raise GoogleTokenExchangeError(
                status=token_resp.status_code,
                error_code=error_code,
                error_description=error_description,
                body=raw_body,
            )
        token_payload = token_resp.json()

        access_token = str(token_payload.get("access_token") or "")
        if not access_token:
            raise GoogleDocsError("google token response missing access_token")

        # connected_email is display-only — a userinfo failure must NOT fail the
        # connect (otherwise a missing email scope 502s the whole OAuth callback).
        userinfo: dict[str, Any] = {}
        try:
            info_resp = await http.get(
                _GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if info_resp.status_code == 200:
                userinfo = info_resp.json()
        except httpx.HTTPError:
            userinfo = {}

    expiry = datetime.now(UTC) + timedelta(seconds=int(token_payload.get("expires_in", 3600)))
    refresh_token = token_payload.get("refresh_token")
    return GoogleTokenBundle(
        access_token=access_token,
        refresh_token=str(refresh_token) if refresh_token else None,
        expiry=expiry,
        scope=str(token_payload.get("scope") or "") or None,
        connected_email=str(userinfo.get("email") or "") or None,
    )


async def refresh_access_token(
    *,
    refresh_token: str,
    client_id: str,
    client_secret: str,
) -> GoogleTokenBundle:
    async with _make_http_client(timeout=20.0) as http:
        resp = await http.post(
            _GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    if resp.status_code == 400:
        raise GoogleReauthRequiredError("google refresh token is no longer valid")
    resp.raise_for_status()
    payload = resp.json()
    new_access_token = str(payload.get("access_token") or "")
    if not new_access_token:
        raise GoogleDocsError("google refresh response missing access_token")
    expiry = datetime.now(UTC) + timedelta(seconds=int(payload.get("expires_in", 3600)))
    maybe_refresh = payload.get("refresh_token")
    return GoogleTokenBundle(
        access_token=new_access_token,
        refresh_token=str(maybe_refresh) if maybe_refresh else refresh_token,
        expiry=expiry,
        scope=str(payload.get("scope") or "") or None,
        connected_email=None,
    )


async def revoke_token(*, token: str) -> None:
    if not token:
        return
    async with _make_http_client(timeout=10.0) as http:
        await http.post(_GOOGLE_REVOKE_URL, params={"token": token})


async def import_google_document(
    *,
    access_token: str,
    document_id: str,
) -> GoogleDocumentRecord:
    async with _make_http_client(timeout=20.0) as http:
        resp = await http.get(
            f"{_GOOGLE_DOCS_BASE_URL}/documents/{document_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    resp.raise_for_status()
    payload = resp.json()
    return GoogleDocumentRecord(
        document_id=str(payload.get("documentId") or document_id),
        title=str(payload.get("title") or "Google Doc"),
        url=_document_url(document_id),
        content=_document_to_markdown(payload),
        created=False,
    )


async def export_google_document(
    *,
    access_token: str,
    title: str,
    content: str,
    document_id: str | None,
) -> GoogleDocumentRecord:
    if document_id:
        # Rename goes through the Drive API (drive.file scope), which only covers
        # files the app CREATED — not a doc imported by URL. So rename can 403 on a
        # linked imported doc. It's cosmetic — make it best-effort and never let it
        # fail the export; the content update below uses the full `documents` scope.
        try:
            effective_title = await _rename_google_document(
                access_token=access_token,
                document_id=document_id,
                title=title,
            )
        except (httpx.HTTPError, GoogleDocsError):
            effective_title = title
        await _replace_google_document_content(
            access_token=access_token,
            document_id=document_id,
            content=content,
        )
        return GoogleDocumentRecord(
            document_id=document_id,
            title=effective_title,
            url=_document_url(document_id),
            content=content,
            created=False,
        )

    async with _make_http_client(timeout=20.0) as http:
        create_resp = await http.post(
            f"{_GOOGLE_DOCS_BASE_URL}/documents",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"title": title},
        )
        create_resp.raise_for_status()
        created_payload = create_resp.json()
    created_document_id = str(created_payload.get("documentId") or "")
    if not created_document_id:
        raise GoogleDocsError("google create response missing documentId")
    folder_id = settings.writing_studio_docs_folder_id
    if folder_id:
        await _move_document_to_folder(
            access_token=access_token,
            document_id=created_document_id,
            folder_id=folder_id,
        )
    await _replace_google_document_content(
        access_token=access_token,
        document_id=created_document_id,
        content=content,
    )
    return GoogleDocumentRecord(
        document_id=created_document_id,
        title=str(created_payload.get("title") or title),
        url=_document_url(created_document_id),
        content=content,
        created=True,
    )


async def _move_document_to_folder(
    *,
    access_token: str,
    document_id: str,
    folder_id: str,
) -> None:
    """Move a Drive file into a specific folder (Drive PATCH files.update).

    Uses addParents / removeParents to reparent the file.  If the move is
    rejected (e.g. ``drive.file`` scope does not cover the target folder) this
    logs a warning and returns without raising so the caller still gets the doc.
    """
    try:
        async with _make_http_client(timeout=10.0) as http:
            resp = await http.patch(
                f"{_GOOGLE_DRIVE_BASE_URL}/files/{document_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "addParents": folder_id,
                    "removeParents": "root",
                    "supportsAllDrives": "true",
                    "fields": "id,parents",
                },
                json={},
            )
        if resp.status_code >= 300:
            logger.warning(
                "Writing Studio: failed to move doc %s into folder %s — "
                "HTTP %s body=%r (doc stays in My Drive root; likely a drive.file scope "
                "limitation — share the folder with the app or broaden OAuth scopes)",
                document_id,
                folder_id,
                resp.status_code,
                resp.text[:400],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Writing Studio: exception moving doc %s into folder %s — %r "
            "(doc stays in My Drive root)",
            document_id,
            folder_id,
            exc,
        )


async def _rename_google_document(
    *,
    access_token: str,
    document_id: str,
    title: str,
) -> str:
    async with _make_http_client(timeout=20.0) as http:
        resp = await http.patch(
            f"{_GOOGLE_DRIVE_BASE_URL}/files/{document_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "id,name"},
            json={"name": title},
        )
    resp.raise_for_status()
    payload = resp.json()
    return str(payload.get("name") or title)


async def _replace_google_document_content(
    *,
    access_token: str,
    document_id: str,
    content: str,
) -> None:
    async with _make_http_client(timeout=20.0) as http:
        doc_resp = await http.get(
            f"{_GOOGLE_DOCS_BASE_URL}/documents/{document_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        doc_resp.raise_for_status()
        payload = doc_resp.json()

        requests: list[dict[str, Any]] = []
        end_index = _document_end_index(payload)
        # Only delete when there's a NON-EMPTY range to delete. A fresh/empty doc has
        # end_index==2 (one trailing newline), so [1, end_index-1] would be [1,1] — an
        # empty range the Docs API rejects (400 → 502). Require end_index > 2.
        if end_index > 2:
            requests.append(
                {
                    "deleteContentRange": {
                        "range": {
                            "startIndex": 1,
                            "endIndex": end_index - 1,
                        }
                    }
                }
            )
        if content:
            requests.append(
                {
                    "insertText": {
                        "location": {"index": 1},
                        "text": content,
                    }
                }
            )
        if requests:
            batch_resp = await http.post(
                f"{_GOOGLE_DOCS_BASE_URL}/documents/{document_id}:batchUpdate",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"requests": requests},
            )
            batch_resp.raise_for_status()


def _document_end_index(document_payload: dict[str, Any]) -> int:
    body = document_payload.get("body")
    if not isinstance(body, dict):
        return 1
    content = body.get("content")
    if not isinstance(content, list) or not content:
        return 1
    last = content[-1]
    if not isinstance(last, dict):
        return 1
    return int(last.get("endIndex") or 1)


def _document_to_markdown(document_payload: dict[str, Any]) -> str:
    body = document_payload.get("body")
    if not isinstance(body, dict):
        return ""
    items = body.get("content")
    if not isinstance(items, list):
        return ""

    lists_payload = document_payload.get("lists")
    lists_map = lists_payload if isinstance(lists_payload, dict) else {}
    blocks: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        paragraph = item.get("paragraph")
        if not isinstance(paragraph, dict):
            continue
        raw_text = _paragraph_text(paragraph)
        text = raw_text.strip()
        if not text:
            continue
        bullet = paragraph.get("bullet")
        if isinstance(bullet, dict):
            blocks.append(("list", f"{_list_prefix(lists_map, bullet)} {text}"))
            continue

        style = paragraph.get("paragraphStyle")
        named_style = style.get("namedStyleType") if isinstance(style, dict) else None
        if named_style == "HEADING_1":
            blocks.append(("paragraph", f"# {text}"))
        elif named_style == "HEADING_2":
            blocks.append(("paragraph", f"## {text}"))
        elif named_style == "HEADING_3":
            blocks.append(("paragraph", f"### {text}"))
        else:
            blocks.append(("paragraph", text))

    rendered: list[str] = []
    previous_kind: str | None = None
    for kind, text in blocks:
        if rendered:
            rendered.append("\n" if kind == previous_kind == "list" else "\n\n")
        rendered.append(text)
        previous_kind = kind
    return "".join(rendered).strip()


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    elements = paragraph.get("elements")
    if not isinstance(elements, list):
        return ""
    parts: list[str] = []
    for element in elements:
        if not isinstance(element, dict):
            continue
        text_run = element.get("textRun")
        if isinstance(text_run, dict):
            content = text_run.get("content")
            if isinstance(content, str):
                parts.append(content.replace("\u000b", "\n"))
    return "".join(parts).rstrip("\n")


def _list_prefix(lists_map: dict[str, Any], bullet: dict[str, Any]) -> str:
    nesting_level = int(bullet.get("nestingLevel") or 0)
    indent = "  " * max(nesting_level, 0)
    list_id = bullet.get("listId")
    glyph_type = ""
    if isinstance(list_id, str):
        list_payload = lists_map.get(list_id)
        if isinstance(list_payload, dict):
            list_props = list_payload.get("listProperties")
            if isinstance(list_props, dict):
                nesting_levels = list_props.get("nestingLevels")
                if isinstance(nesting_levels, list) and nesting_level < len(nesting_levels):
                    nesting_payload = nesting_levels[nesting_level]
                    if isinstance(nesting_payload, dict):
                        glyph_type = str(nesting_payload.get("glyphType") or "")
    if any(token in glyph_type for token in ("DECIMAL", "ALPHA", "ROMAN")):
        return f"{indent}1."
    return f"{indent}-"


def compact_preview_text(value: str, limit: int = 900) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit].rstrip()}..."


def _document_url(document_id: str) -> str:
    return f"https://docs.google.com/document/d/{document_id}/edit"
