"""Enablement indexing webhook — ingest endpoint for the Apps Script pipeline.

The enablement knowledge that powers Kai (Chiron) lives in 4 curated Google
Sheets + an evergreen "Indexed Docs" Drive folder (see
``briefs/enablement-sheet-configs.md``). Rather than have the server make live
Google API calls per query (slow, token-heavy, broad scopes), a Google Apps
Script hosted on ``amiracentral@amiralearning.com`` reads each source natively,
normalises one record per asset, and POSTs batches here. We embed + upsert into
``enablement_assets``; Kai then searches that table (snappy + cheap). The server
needs zero Drive scopes.

Auth (two layers):
  1. Cloudflare Access (edge): the Apps Script must carry a CF Access service
     token so the request reaches the app at all. Configured in the CF Zero
     Trust dashboard — see the Apps Script deploy runbook.
  2. App layer (here): the request must carry ``X-Enablement-Token`` matching
     ``settings.enablement_webhook_secret``. Empty secret = endpoint disabled
     (fail-closed).

Idempotency + freshness:
  - Each asset is upserted by its stable ``key`` (-> ``drive_file_id``); re-runs
    update in place, never duplicate.
  - ``full_refresh=true`` soft-archives any *active* asset of the same
    ``source_sheet`` not present in the batch (status -> "archived"). This is a
    supersession, not a DELETE — the lossless rule holds, and Kai's search
    excludes archived rows so retired sheet rows stop surfacing.
"""

from __future__ import annotations

import hmac
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

import artemis.db as _db
from artemis.config import settings
from artemis.enablement.models import EnablementAsset

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enablement", tags=["enablement"])


# ── Request models ─────────────────────────────────────────────────────────────


class LinkIn(BaseModel):
    """One link on an asset, with the flags Kai's surfacing rules read."""

    role: str  # e.g. "deck", "handout_customer", "handout_editable", "pdf", "web", "script", "webinar", "doc"
    label: str  # human label Kai shows, e.g. "Customer handout", "PDF", "Editable (make a copy)"
    url: str
    visibility: Literal["customer", "internal"] = "customer"
    on_request: bool = False  # surface only if the CSM explicitly asks
    make_copy: bool = False  # view-only; CSM must copy before editing


class AssetIn(BaseModel):
    key: str = Field(..., min_length=1)  # stable idempotency key -> drive_file_id
    asset_type: str | None = None
    title: str | None = None
    summary: str | None = None
    audience: str | None = None
    tags: list[str] = Field(default_factory=list)
    searchable_text: str | None = None
    links: list[LinkIn] = Field(default_factory=list)
    requires_copy: bool = False
    status: str = "active"
    confidence_label: str | None = None
    drive_link: str | None = None  # explicit default link; else derived from links
    source_row: str | None = None
    source_scope: str = "enablement"
    extra: dict[str, Any] | None = None


class IngestBatch(BaseModel):
    source_sheet: str = Field(..., min_length=1)
    full_refresh: bool = False
    # When full_refresh and the source is chunked across multiple POSTs, the FIRST
    # chunk carries the complete key set so we archive only rows truly gone from the
    # sheet — not rows that a later chunk will re-add. None = fall back to this batch's
    # keys (single-chunk case).
    keep_keys: list[str] | None = None
    assets: list[AssetIn] = Field(default_factory=list)


class IngestResult(BaseModel):
    source_sheet: str
    upserted: int
    archived: int
    embedded: int


# ── Helpers ──────────────────────────────────────────────────────────────────


def _clean(s: str | None) -> str | None:
    """Return the trimmed string, or None if blank/whitespace-only."""
    if s is None:
        return None
    stripped = s.strip()
    return stripped if stripped else None


def _require_secret(token: str | None) -> None:
    """Fail-closed shared-secret check (constant-time)."""
    secret = settings.enablement_webhook_secret
    if not secret:
        raise HTTPException(
            status_code=503,
            detail={"error": "enablement ingest disabled", "code": "ingest_disabled"},
        )
    if not token or not hmac.compare_digest(token, secret):
        raise HTTPException(
            status_code=401,
            detail={"error": "invalid enablement token", "code": "unauthorized"},
        )


def _default_link(asset: AssetIn) -> str | None:
    """Backward-compatible primary link: first customer, always-on link."""
    if asset.drive_link:
        return asset.drive_link
    for link in asset.links:
        if link.visibility == "customer" and not link.on_request:
            return link.url
    return asset.links[0].url if asset.links else None


def _embedding_text(asset: AssetIn) -> str:
    parts: list[str] = []
    if asset.title:
        parts.append(asset.title)
    if asset.summary:
        parts.append(asset.summary)
    if asset.tags:
        parts.extend(t for t in asset.tags if t)
    if asset.audience:
        parts.append(asset.audience)
    if asset.searchable_text:
        # truncate — enough signal, avoids ballooning the embed input
        parts.append(asset.searchable_text[:4000])
    return " ".join(parts).strip()


async def _compute_embedding(text: str) -> list[float] | None:
    if not text.strip():
        return None
    try:
        from artemis.memory.embeddings import MiniLMProvider

        provider = MiniLMProvider()
        return await provider.embed(text)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("enablement ingest: embedding failed (%r) — storing NULL", exc)
        return None


# ── Endpoint ───────────────────────────────────────────────────────────────────


@router.post("/ingest", response_model=IngestResult)
async def ingest(
    batch: IngestBatch,
    x_enablement_token: Annotated[str | None, Header()] = None,
) -> IngestResult:
    """Ingest a batch of normalised enablement assets for one source sheet/folder."""
    _require_secret(x_enablement_token)

    now = datetime.now(UTC)
    upserted = 0
    embedded = 0
    seen_keys: list[str] = []

    async with _db.SessionLocal() as session:
        for asset in batch.assets:
            seen_keys.append(asset.key)
            embed_vec = await _compute_embedding(_embedding_text(asset))
            if embed_vec is not None:
                embedded += 1

            links_json = [link.model_dump() for link in asset.links]

            c_title = _clean(asset.title)
            c_summary = _clean(asset.summary)
            c_audience = _clean(asset.audience)
            c_asset_type = _clean(asset.asset_type)
            c_confidence_label = _clean(asset.confidence_label)
            c_drive_link = _clean(_default_link(asset))
            c_source_row = _clean(asset.source_row)

            insert_stmt = pg_insert(EnablementAsset).values(
                drive_file_id=asset.key,
                asset_name=c_title,
                type=c_asset_type,
                drive_link=c_drive_link,
                title=c_title,
                summary=c_summary,
                tags=asset.tags or None,
                audience=c_audience,
                status=asset.status,
                confidence_label=c_confidence_label,
                source_scope=asset.source_scope or "enablement",
                links=links_json or None,
                searchable_text=asset.searchable_text,
                source_sheet=batch.source_sheet,
                source_row=c_source_row,
                requires_copy=asset.requires_copy,
                embedding=embed_vec,
                extra=asset.extra,
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
                    "status": insert_stmt.excluded.status,
                    "confidence_label": insert_stmt.excluded.confidence_label,
                    "source_scope": insert_stmt.excluded.source_scope,
                    "links": insert_stmt.excluded.links,
                    "searchable_text": insert_stmt.excluded.searchable_text,
                    "source_sheet": insert_stmt.excluded.source_sheet,
                    "source_row": insert_stmt.excluded.source_row,
                    "requires_copy": insert_stmt.excluded.requires_copy,
                    "embedding": insert_stmt.excluded.embedding,
                    "extra": insert_stmt.excluded.extra,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)
            upserted += 1

        archived = 0
        if batch.full_refresh:
            # Soft-archive (supersede, never DELETE) active rows from this source
            # that were not in this batch — handles sheet rows the team removed.
            # Use the full key set (keep_keys) when the source is chunked, so a
            # later chunk's rows aren't archived by an earlier chunk.
            keep = batch.keep_keys if batch.keep_keys is not None else seen_keys
            archive_stmt = (
                update(EnablementAsset)
                .where(EnablementAsset.source_sheet == batch.source_sheet)
                .where(EnablementAsset.status != "archived")
                .where(EnablementAsset.drive_file_id.notin_(keep or [""]))
                .values(status="archived", updated_at=now)
            )
            result = await session.execute(archive_stmt)
            archived = getattr(result, "rowcount", 0) or 0

        await session.commit()

    _logger.info(
        "enablement ingest: source=%s upserted=%d embedded=%d archived=%d",
        batch.source_sheet,
        upserted,
        embedded,
        archived,
    )
    return IngestResult(
        source_sheet=batch.source_sheet,
        upserted=upserted,
        archived=archived,
        embedded=embedded,
    )
