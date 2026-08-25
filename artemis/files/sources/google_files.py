"""Read Google Docs, Sheets and Slides that people share with the agents.

Two things make this different from a Slack download.

**There are no bytes.** A native Google file has no downloadable payload; it must
be rendered by Drive's export endpoint (Docs, Slides) or read tab-by-tab through
the Sheets API. Drive's export renders only a spreadsheet's FIRST tab, which is
why Sheets does not go through it.

**Access is the usual failure, and Drive disguises it.** A file the credential
cannot see returns **404, not 403** -- indistinguishable from a file that does
not exist. Every 404 here is therefore reported as "either it does not exist or
it is not shared with me", because claiming a definite cause would be a guess.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.files.extract.base import (
    MAX_TABULAR_SAMPLE_ROWS,
    AccessDeniedError,
    ExtractedFile,
    FileParseError,
    TabularShape,
    cap_text,
)

logger = logging.getLogger(__name__)

_DRIVE = "https://www.googleapis.com/drive/v3"
_SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# The scope that separates "can read what people share with me" from "can only
# see files I created myself". Selection below keys on this rather than on which
# credential was refreshed most recently.
_REQUIRED_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

_URL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("document", re.compile(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]{20,})")),
    ("spreadsheet", re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]{20,})")),
    ("presentation", re.compile(r"docs\.google\.com/presentation/d/([a-zA-Z0-9_-]{20,})")),
    ("drive", re.compile(r"drive\.google\.com/file/d/([a-zA-Z0-9_-]{20,})")),
    ("drive", re.compile(r"drive\.google\.com/open\?id=([a-zA-Z0-9_-]{20,})")),
)


def find_google_links(text: str) -> list[tuple[str, str]]:
    """Find Google file links in free text, as ``(kind, file_id)`` pairs.

    Pasting a link is at least as common as uploading a file, and Slack wraps
    URLs as ``<https://...|label>`` -- the patterns match the bare URL anywhere
    in the string, so the wrapping is irrelevant. Duplicates are collapsed so a
    link that appears twice is fetched once.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kind, pattern in _URL_PATTERNS:
        for match in pattern.finditer(text or ""):
            file_id = match.group(1)
            if file_id not in seen:
                seen.add(file_id)
                found.append((kind, file_id))
    return found


async def resolve_agent_google_token(session: AsyncSession) -> str:
    """Return an access token for the shared agent Google account.

    Selects the marketing credential that actually HOLDS `drive.readonly`,
    rather than the most recently updated one. Three accounts share
    ``purpose='marketing'`` and only the agent mailbox has been re-consented; a
    plain recency sort would silently switch to a colleague's credential the
    moment a background refresh touched it, and every read would start 404ing
    for reasons no log line would explain.
    """
    from artemis.google_docs.client import GoogleReauthRequiredError, refresh_access_token
    from artemis.google_docs.models import GoogleCredential
    from artemis.google_integration import resolve_google_oauth_client_config

    rows = (
        (
            await session.execute(
                select(GoogleCredential)
                .where(GoogleCredential.purpose == "marketing")
                .order_by(GoogleCredential.updated_at.desc())
            )
        )
        .scalars()
        .all()
    )
    capable = [r for r in rows if _REQUIRED_SCOPE in (r.scope or "")]
    if not capable:
        raise AccessDeniedError(
            "No Google account is connected with permission to read shared files. "
            "Reconnect the Marketing Google (Docs) integration to grant drive.readonly."
        )
    credential = capable[0]

    now = datetime.now(UTC)
    if credential.expiry and credential.expiry > now + timedelta(seconds=60):
        return credential.access_token

    if not credential.refresh_token:
        raise AccessDeniedError(
            f"The Google account {credential.connected_email} needs to be reconnected "
            "(no refresh token stored)."
        )

    client_config = await resolve_google_oauth_client_config(session)
    try:
        refreshed = await refresh_access_token(
            refresh_token=credential.refresh_token,
            client_id=client_config.client_id,
            client_secret=client_config.client_secret,
        )
    except GoogleReauthRequiredError as exc:
        raise AccessDeniedError(
            f"The Google account {credential.connected_email} needs to be reconnected: {exc}"
        ) from exc
    except httpx.HTTPError as exc:
        raise AccessDeniedError(f"Could not refresh Google access ({type(exc).__name__}).") from exc

    credential.access_token = refreshed.access_token
    credential.refresh_token = refreshed.refresh_token
    credential.expiry = refreshed.expiry
    if refreshed.scope:
        credential.scope = refreshed.scope
    credential.updated_at = now
    await session.flush()
    return credential.access_token


def _access_error(file_id: str, status: int, name: str = "") -> AccessDeniedError:
    label = name or f"Google file {file_id}"
    if status == 404:
        return AccessDeniedError(
            f"{label} could not be opened: Drive reports it does not exist OR it is not "
            "shared with the agent's Google account. Drive returns the same 404 for both, "
            "so this cannot be narrowed further from here -- sharing the file resolves it "
            "if it does exist."
        )
    if status in (401, 403):
        return AccessDeniedError(
            f"{label} exists but the agent's Google account is not permitted to read it "
            f"(HTTP {status}). Share it with that account to grant access."
        )
    return AccessDeniedError(f"{label} could not be read (Google returned HTTP {status}).")


async def _get_metadata(http: httpx.AsyncClient, file_id: str, token: str) -> tuple[str, str]:
    response = await http.get(
        f"{_DRIVE}/files/{file_id}",
        params={"fields": "name,mimeType", "supportsAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code >= 400:
        raise _access_error(file_id, response.status_code)
    body = response.json()
    return str(body.get("name") or file_id), str(body.get("mimeType") or "")


async def fetch_google_file(
    file_id: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> ExtractedFile:
    """Read one Google file by id, dispatching on its real Drive mimeType."""
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True)
    try:
        name, mimetype = await _get_metadata(http, file_id, token)
        url = f"https://drive.google.com/open?id={file_id}"

        if mimetype == "application/vnd.google-apps.spreadsheet":
            return await _read_sheet(http, file_id, token, name=name, source_url=url)
        if mimetype == "application/vnd.google-apps.document":
            return await _export(
                http,
                file_id,
                token,
                name=name,
                mimetype=mimetype,
                export_as="text/plain",
                source_url=url,
            )
        if mimetype == "application/vnd.google-apps.presentation":
            return await _export(
                http,
                file_id,
                token,
                name=name,
                mimetype=mimetype,
                export_as="text/plain",
                source_url=url,
            )
        # A non-native file stored in Drive (an uploaded xlsx, pdf, docx) has real
        # bytes and goes through the ordinary extractors.
        return await _download_binary(
            http, file_id, token, name=name, mimetype=mimetype, source_url=url
        )
    finally:
        if owns_client:
            await http.aclose()


async def _export(
    http: httpx.AsyncClient,
    file_id: str,
    token: str,
    *,
    name: str,
    mimetype: str,
    export_as: str,
    source_url: str,
) -> ExtractedFile:
    response = await http.get(
        f"{_DRIVE}/files/{file_id}/export",
        params={"mimeType": export_as},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code >= 400:
        raise _access_error(file_id, response.status_code, name)

    body = response.text
    if not body.strip():
        raise FileParseError(f"{name} opened but contains no text.")
    text, truncated = cap_text(body)
    return ExtractedFile(
        filename=name,
        mimetype=mimetype,
        kind="document",
        text=text,
        size_bytes=len(response.content),
        source="google",
        source_url=source_url,
        truncated=truncated,
    )


async def _download_binary(
    http: httpx.AsyncClient,
    file_id: str,
    token: str,
    *,
    name: str,
    mimetype: str,
    source_url: str,
) -> ExtractedFile:
    from artemis.files.extract import extract

    response = await http.get(
        f"{_DRIVE}/files/{file_id}",
        params={"alt": "media", "supportsAllDrives": "true"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code >= 400:
        raise _access_error(file_id, response.status_code, name)
    return extract(
        response.content,
        filename=name,
        mimetype=mimetype,
        source="google",
        source_url=source_url,
    )


async def _read_sheet(
    http: httpx.AsyncClient,
    file_id: str,
    token: str,
    *,
    name: str,
    source_url: str,
) -> ExtractedFile:
    """Read EVERY tab of a spreadsheet.

    Drive's export endpoint would be simpler but renders only the first tab, so a
    multi-tab workbook would be silently reported as if the other tabs did not
    exist -- the kind of quiet omission that is worse than an error.
    """
    headers = {"Authorization": f"Bearer {token}"}
    meta = await http.get(
        f"{_SHEETS}/{file_id}", params={"fields": "sheets.properties.title"}, headers=headers
    )
    if meta.status_code >= 400:
        raise _access_error(file_id, meta.status_code, name)

    titles = [
        str(sheet.get("properties", {}).get("title") or "")
        for sheet in meta.json().get("sheets", [])
    ]
    titles = [t for t in titles if t]
    if not titles:
        raise FileParseError(f"{name} is a spreadsheet with no readable tabs.")

    values = await http.get(
        f"{_SHEETS}/{file_id}/values:batchGet",
        # Repeated `ranges` params (one per tab) — a list of pairs, not a dict,
        # because a dict cannot carry the same key more than once.
        params=[*(("ranges", t) for t in titles), ("majorDimension", "ROWS")],
        headers=headers,
    )
    if values.status_code >= 400:
        raise _access_error(file_id, values.status_code, name)

    shapes: list[TabularShape] = []
    for title, value_range in zip(titles, values.json().get("valueRanges", []), strict=False):
        rows = [[str(c) for c in row] for row in value_range.get("values", []) if any(row)]
        if not rows:
            continue
        header, *body = rows
        shapes.append(
            TabularShape(
                columns=[c.strip() for c in header],
                total_rows=len(body),
                sample_rows=body[:MAX_TABULAR_SAMPLE_ROWS],
                sheet_name=title,
                truncated=len(body) > MAX_TABULAR_SAMPLE_ROWS,
            )
        )

    if not shapes:
        raise FileParseError(f"{name} opened as a spreadsheet but every tab is empty.")

    from artemis.files.extract.tabular import _render_table

    text, truncated = cap_text("\n\n".join(_render_table(s, name) for s in shapes))
    notes = (
        [f"Spreadsheet has {len(shapes)} tabs: {', '.join(s.sheet_name or '?' for s in shapes)}."]
        if len(shapes) > 1
        else []
    )
    return ExtractedFile(
        filename=name,
        mimetype="application/vnd.google-apps.spreadsheet",
        kind="tabular",
        text=text,
        size_bytes=len(values.content),
        source="google",
        source_url=source_url,
        tables=shapes,
        truncated=truncated or any(s.truncated for s in shapes),
        notes=notes,
    )
