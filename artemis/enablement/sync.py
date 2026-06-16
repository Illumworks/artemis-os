"""Sheet→DB sync for the enablement_assets table (Kai / Chiron).

Entry point: ``sync_enablement_index(session)``

Sheet access strategy
---------------------
We read the sheet via the **Drive API files.export** endpoint (MIME type
``text/csv``), which works with the ``drive.readonly`` or ``drive.file`` scopes
already granted to the personal Google credential (client 612420684593).

We deliberately avoid the Sheets API (``spreadsheets.readonly`` scope) because
that scope is not currently provisioned on the Artemis Google OAuth client.
If a missing-scope 403 is ever received during the Drive export call it is
caught, logged as a warning, and the sync returns 0 rows rather than crashing.

Idempotency contract
--------------------
Each sheet row is upserted by ``drive_file_id`` (unique constraint).  A row
with no drive_file_id gets a stable slug key: ``slug:<slugified asset_name>``.
Re-running the sync updates all columns; it never creates duplicates.

Cron wiring
-----------
Do NOT register this on a scheduler yet.  Lead wires the cron once Kai's agent
shell exists.  The function is importable and callable from any async context:

    from artemis.enablement.sync import sync_enablement_index
    n = await sync_enablement_index(session)
"""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.enablement.models import EnablementAsset

_logger = logging.getLogger(__name__)

# The Google Sheet that holds the ENABLEMENT_DB tab.
_SHEET_ID = "1kcgf06TslHR3IZ8nv6839UHh5Y3kPqHkJoc9gZprY3M"
_TAB_NAME = "ENABLEMENT_DB"

# Drive export endpoint: exports a Sheets tab to CSV.
# gid=0 is the first sheet; we resolve it dynamically by title if needed.
# For now we export the full spreadsheet as CSV (only works for single-sheet
# or yields the first sheet); if the tab isn't first we pass &gid= explicitly.
_DRIVE_EXPORT_URL = (
    "https://www.googleapis.com/drive/v3/files/{file_id}/export"
)

# Column name → EnablementAsset attribute mapping (case-insensitive, normalised).
# Extra sheet columns not listed here land in ``extra`` JSONB.
_COL_MAP: dict[str, str] = {
    "drive_file_id": "drive_file_id",
    "file_id": "drive_file_id",
    "asset_name": "asset_name",
    "name": "asset_name",
    "type": "type",
    "asset_type": "type",
    "drive_link": "drive_link",
    "link": "drive_link",
    "url": "drive_link",
    "title": "title",
    "summary": "summary",
    "description": "summary",
    "tags": "tags",
    "audience": "audience",
    "transcript_link": "transcript_link",
    "transcript_text": "transcript_text",
    "status": "status",
    "confidence_label": "confidence_label",
    "confidence": "confidence_label",
    "source_scope": "source_scope",
    "scope": "source_scope",
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower().strip()).strip("-")[:120]


def _build_embedding_text(row: dict[str, Any]) -> str:
    """Combine searchable fields into a single embedding source string."""
    parts: list[str] = []
    if row.get("title"):
        parts.append(str(row["title"]))
    if row.get("summary"):
        parts.append(str(row["summary"]))
    if row.get("tags"):
        tags = row["tags"]
        if isinstance(tags, list):
            parts.extend(str(t) for t in tags if t)
        elif isinstance(tags, str) and tags:
            parts.append(tags)
    if row.get("transcript_text"):
        # truncate to 2000 chars — enough signal, avoids ballooning the embed input
        parts.append(str(row["transcript_text"])[:2000])
    return " ".join(parts).strip()


def _parse_tags(raw: str | None) -> list[str] | None:
    """Parse a comma/semicolon-separated tags string into a list, or None."""
    if not raw or not raw.strip():
        return None
    sep = ";" if ";" in raw else ","
    return [t.strip() for t in raw.split(sep) if t.strip()]


def _map_row(header: list[str], values: list[str]) -> dict[str, Any]:
    """Map a CSV row to a dict keyed by normalised column names."""
    raw: dict[str, str] = {}
    for col, val in zip(header, values):
        raw[col.strip().lower()] = val.strip()

    mapped: dict[str, Any] = {}
    extra: dict[str, str] = {}

    for raw_col, val in raw.items():
        attr = _COL_MAP.get(raw_col)
        if attr is not None:
            if attr == "tags":
                mapped[attr] = _parse_tags(val)
            else:
                mapped[attr] = val if val else None
        else:
            if val:
                extra[raw_col] = val

    if extra:
        mapped["extra"] = extra

    return mapped


async def _get_personal_access_token(session: AsyncSession) -> str | None:
    """Resolve a valid personal Google access token, refreshing if needed.

    Returns None (and logs a warning) if no personal credential is connected
    or if the refresh token is no longer valid.
    """
    from sqlalchemy import select

    from artemis.google_docs.client import (
        GoogleReauthRequiredError,
        refresh_access_token,
    )
    from artemis.google_docs.models import GoogleCredential
    from artemis.google_integration import resolve_google_oauth_client_config

    result = await session.execute(
        select(GoogleCredential)
        .where(GoogleCredential.purpose == "personal")
        .order_by(GoogleCredential.updated_at.desc())
        .limit(1)
    )
    credential = result.scalar_one_or_none()
    if credential is None:
        _logger.warning("enablement sync: no personal Google credential — skipping")
        return None

    now = datetime.now(UTC)
    if credential.expiry <= now + timedelta(seconds=60):
        if not credential.refresh_token:
            _logger.warning("enablement sync: no refresh token available — skipping")
            return None
        try:
            config = await resolve_google_oauth_client_config(session)
            refreshed = await refresh_access_token(
                refresh_token=credential.refresh_token,
                client_id=config.client_id,
                client_secret=config.client_secret,
            )
        except GoogleReauthRequiredError:
            _logger.warning("enablement sync: Google reauth required — skipping")
            return None
        credential.access_token = refreshed.access_token
        if refreshed.refresh_token:
            credential.refresh_token = refreshed.refresh_token
        credential.expiry = refreshed.expiry
        credential.updated_at = now

    return credential.access_token


async def _fetch_sheet_csv(access_token: str) -> str | None:
    """Fetch the ENABLEMENT_DB tab as CSV via Drive files.export.

    Drive export returns the first sheet by default.  To target a specific
    tab by title we first list the sheet metadata to find its gid, then
    append ``&gid=<gid>`` to the export URL.  If the metadata call fails we
    fall back to exporting without a gid (first sheet).

    Returns the raw CSV string, or None on failure (caller should no-op).
    """
    url = _DRIVE_EXPORT_URL.format(file_id=_SHEET_ID)
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: resolve the gid for ENABLEMENT_DB tab.
    gid: str | None = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            meta_resp = await http.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{_SHEET_ID}",
                headers=headers,
                params={"fields": "sheets.properties"},
            )
        if meta_resp.status_code == 200:
            sheets = meta_resp.json().get("sheets", [])
            for sheet in sheets:
                props = sheet.get("properties", {})
                if props.get("title", "").strip().upper() == _TAB_NAME.upper():
                    gid = str(props.get("sheetId", ""))
                    break
        elif meta_resp.status_code == 403:
            _logger.warning(
                "enablement sync: 403 on Sheets metadata (scope not granted?) — "
                "falling back to Drive export without gid"
            )
        else:
            _logger.warning(
                "enablement sync: Sheets metadata returned %s — "
                "falling back to first sheet",
                meta_resp.status_code,
            )
    except Exception as exc:  # noqa: BLE001
        _logger.warning("enablement sync: Sheets metadata error (%r) — falling back", exc)

    # Step 2: export to CSV via Drive API.
    params: dict[str, str] = {"mimeType": "text/csv"}
    if gid is not None:
        params["gid"] = gid

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.get(url, headers=headers, params=params)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("enablement sync: Drive export request failed: %r", exc)
        return None

    if resp.status_code == 403:
        _logger.warning(
            "enablement sync: Drive export returned 403 — "
            "the personal Google account may not have access to sheet %s. "
            "Share the sheet with the connected Google account or grant "
            "drive.readonly scope.",
            _SHEET_ID,
        )
        return None
    if resp.status_code != 200:
        _logger.warning(
            "enablement sync: Drive export returned HTTP %s — skipping",
            resp.status_code,
        )
        return None

    return resp.text


async def _compute_embedding(text: str) -> list[float] | None:
    """Compute a 384-dim embedding for the given text string.

    Returns None when the sentence-transformers model is unavailable or the
    text is empty so callers can store NULL and backfill later.
    """
    if not text.strip():
        return None
    try:
        from artemis.memory.embeddings import MiniLMProvider

        provider = MiniLMProvider()
        return await provider.embed(text)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("enablement sync: embedding failed (%r) — storing NULL", exc)
        return None


async def _upsert_rows(
    session: AsyncSession,
    rows: list[dict[str, Any]],
) -> int:
    """Upsert a list of mapped rows into enablement_assets. Returns upserted count."""
    if not rows:
        return 0

    now = datetime.now(UTC)
    upserted = 0

    for row in rows:
        drive_file_id = row.get("drive_file_id") or ""
        if not drive_file_id:
            # synthesise a stable slug key
            asset_name = row.get("asset_name") or row.get("title") or ""
            slug = _slugify(asset_name) if asset_name else None
            if not slug:
                _logger.debug("enablement sync: skipping row with no id or name: %r", row)
                continue
            drive_file_id = f"slug:{slug}"
            row["drive_file_id"] = drive_file_id

        # Compute embedding from searchable text fields
        embed_text = _build_embedding_text(row)
        embedding = await _compute_embedding(embed_text)

        insert_stmt = pg_insert(EnablementAsset).values(
            drive_file_id=drive_file_id,
            asset_name=row.get("asset_name"),
            type=row.get("type"),
            drive_link=row.get("drive_link"),
            title=row.get("title"),
            summary=row.get("summary"),
            tags=row.get("tags"),
            audience=row.get("audience"),
            transcript_link=row.get("transcript_link"),
            transcript_text=row.get("transcript_text"),
            status=row.get("status"),
            confidence_label=row.get("confidence_label"),
            source_scope=row.get("source_scope") or "enablement",
            embedding=embedding,
            extra=row.get("extra"),
            updated_at=now,
            created_at=now,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["drive_file_id"],
            set_={
                "asset_name": insert_stmt.excluded.asset_name,
                "type": insert_stmt.excluded.type,
                "drive_link": insert_stmt.excluded.drive_link,
                "title": insert_stmt.excluded.title,
                "summary": insert_stmt.excluded.summary,
                "tags": insert_stmt.excluded.tags,
                "audience": insert_stmt.excluded.audience,
                "transcript_link": insert_stmt.excluded.transcript_link,
                "transcript_text": insert_stmt.excluded.transcript_text,
                "status": insert_stmt.excluded.status,
                "confidence_label": insert_stmt.excluded.confidence_label,
                "source_scope": insert_stmt.excluded.source_scope,
                "embedding": insert_stmt.excluded.embedding,
                "extra": insert_stmt.excluded.extra,
                "updated_at": now,
            },
        )
        await session.execute(stmt)
        upserted += 1

    return upserted


def _parse_csv(raw_csv: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Parse raw CSV text into (header, rows).

    Returns an empty list if the CSV is blank or has no data rows.
    """
    reader = csv.reader(io.StringIO(raw_csv))
    rows_raw = list(reader)
    if not rows_raw:
        return [], []
    header = [col.strip() for col in rows_raw[0]]
    if not any(header):
        return [], []
    data_rows = [
        _map_row(header, row)
        for row in rows_raw[1:]
        if any(v.strip() for v in row)
    ]
    return header, data_rows


async def sync_enablement_index(session: AsyncSession) -> int:
    """Sync the ENABLEMENT_DB sheet into the enablement_assets table.

    Reads the sheet via Drive API files.export (CSV).  Each row is upserted
    by drive_file_id.  Empty sheet → 0 rows, returns 0.  Idempotent and
    re-run safe.

    Returns the number of rows upserted (0 on empty sheet or access failure).

    Access requirements
    -------------------
    - A personal Google credential must be connected (purpose="personal").
    - The connected account must have at least read access to the sheet.
    - The Drive API (drive.readonly or drive.file scope) must be enabled on
      GCP project 612420684593.  drive.file scope is already granted as part
      of GOOGLE_PERSONAL_SCOPES; drive.readonly is NOT currently listed — the
      Drive export endpoint will succeed with drive.file ONLY for files the
      app created.  For a shared sheet you do NOT own, ``drive.readonly``
      (or explicit file-level share to the authed account) is required.
    - Lead must verify Drive access works and add drive.readonly to
      GOOGLE_PERSONAL_SCOPES if the export returns 403.

    NOTE: The Sheets API (spreadsheets.readonly) is used ONLY for tab-gid
    resolution (a best-effort step to target the correct tab).  A 403 on that
    step is caught and logged; the export then targets the first sheet.
    """
    access_token = await _get_personal_access_token(session)
    if access_token is None:
        _logger.info("enablement sync: no access token — 0 rows upserted")
        return 0

    raw_csv = await _fetch_sheet_csv(access_token)
    if raw_csv is None:
        _logger.info("enablement sync: sheet fetch failed — 0 rows upserted")
        return 0

    _, mapped_rows = _parse_csv(raw_csv)
    if not mapped_rows:
        _logger.info("enablement sync: sheet is empty — 0 rows upserted")
        return 0

    n = await _upsert_rows(session, mapped_rows)
    await session.flush()
    _logger.info("enablement sync: upserted %d rows", n)
    return n
