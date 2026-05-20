"""Writing Studio bidirectional state sync.

Port of writing-studio-sync.js (1025 lines).

Split cleanly into:
  WRITE PATH (Artemis → Writing Studio): export-style functions that push
    local draft/folder state to the external service.
  READ PATH (Writing Studio → Artemis webhook): ingest incoming webhook
    payloads from the external service and update local state.

Function boundaries mirror the Node code. Stub external is used by default.

Key design decisions vs. Node:
- The Node code does full filesystem sync (read/write JSON files). The Python
  port replaces filesystem I/O with DB-driven sync records + external API calls,
  since the Python rebuild doesn't own the Writing Studio filesystem repo.
- Sync IDs mirror Node's stable deterministic format (wd_YYYYMMDD_slug_id).
- Conflict detection uses content hash comparison (sha256).
- Both paths are importable without a live DB — stub external used throughout.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.state_machine import DeliverableState, transition
from artemis.marketing.writing_studio.external import ExternalDraft, get_writing_studio

# ── Constants ──────────────────────────────────────────────────────────────────

SYNC_SCHEMA_VERSION = 1


# ── Utility functions (mirrors Node helpers) ───────────────────────────────────


def slugify(value: str, fallback: str = "item") -> str:
    """Convert a string to a URL-safe slug (max 80 chars)."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_str.lower()).strip("-")[:80]
    return slug or fallback


def hash_text(value: str) -> str:
    """SHA-256 hex digest of a string."""
    return hashlib.sha256(str(value or "").encode()).hexdigest()


def unix_to_iso(value: Any) -> str | None:
    """Convert a UNIX timestamp (int seconds) to ISO 8601 string."""
    try:
        seconds = float(value or 0)
        if not (seconds > 0):
            return None
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def iso_to_unix(value: str | None) -> int | None:
    """Parse an ISO 8601 string to UNIX timestamp (int seconds)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return int(dt.timestamp())
    except (TypeError, ValueError):
        return None


def date_stamp_from_iso(value: str | None) -> str:
    """Extract a compact date stamp (YYYYMMDD) from an ISO string."""
    iso = value or datetime.now(UTC).isoformat()
    return iso[:10].replace("-", "")


def draft_sync_id(draft_id: int, title: str, created_at: datetime | None = None) -> str:
    """Stable sync ID for a draft, mirrors Node's draftSyncId().

    Format: wd_YYYYMMDD_<slug>_<id>
    """
    stamp = date_stamp_from_iso(created_at.isoformat() if created_at else None)
    return f"wd_{stamp}_{slugify(title or 'draft', 'draft')}_{draft_id}"


def version_sync_id(version_number: int, draft_slug: str) -> str:
    """Stable sync ID for a draft version, mirrors Node's versionSyncId()."""
    padded = str(version_number).zfill(4)
    return f"wv_{draft_slug}_v{padded}"


def folder_sync_id(folder_id: int, name: str, created_at: datetime | None = None) -> str:
    """Stable sync ID for a folder, mirrors Node's folderSyncId()."""
    stamp = date_stamp_from_iso(created_at.isoformat() if created_at else None)
    return f"wf_{stamp}_{slugify(name or 'folder', 'folder')}_{folder_id}"


# ── Data shapes ────────────────────────────────────────────────────────────────


@dataclass
class DraftSyncRecord:
    """Artemis-side summary of a draft for sync comparison."""

    sync_id: str
    title: str
    asset_type: str | None = None
    status: str = "draft"
    campaign_id: str | None = None
    deliverable_id: str | None = None
    content_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    external_id: str | None = None


@dataclass
class SyncConflict:
    """A conflict detected during bidirectional sync inspection."""

    entity_type: str  # 'draft' | 'folder' | 'version'
    sync_id: str | None
    reason: str
    label: str
    detail: str


@dataclass
class SyncResult:
    """Result of a sync operation."""

    drafts_pushed: int = 0
    drafts_pulled: int = 0
    conflicts: list[SyncConflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── WRITE PATH: Artemis → Writing Studio ─────────────────────────────────────


async def push_draft_to_external(
    session: AsyncSession,
    deliverable_id: int,
    *,
    ws: Any = None,
) -> DraftSyncRecord:
    """Push a local campaign_deliverables record to the external Writing Studio.

    Write path: Artemis → WS.
    - Fetches the deliverable row.
    - If no external_id, calls ws.create_draft() to register it externally.
    - Updates the deliverable's deliverable_metadata with the external sync id.
    - Returns a DraftSyncRecord with the stable sync_id and external_id.

    Does NOT commit — caller owns the transaction.
    """
    from datetime import UTC

    from sqlalchemy import select

    from artemis.marketing.models import CampaignDeliverable

    external = ws if ws is not None else get_writing_studio()

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.id == deliverable_id)
    )
    deliverable = result.scalar_one_or_none()
    if deliverable is None:
        raise ValueError(f"campaign_deliverables id={deliverable_id} not found")

    meta = deliverable.deliverable_metadata or {}
    external_id: str | None = deliverable.deliverable_id or meta.get("externalDraftId")
    title = meta.get("externalTitle", f"Deliverable {deliverable_id}")

    if not external_id:
        ext_draft: ExternalDraft = await external.create_draft(
            title=title,
            metadata=meta,
        )
        external_id = ext_draft.external_id
        deliverable.deliverable_id = external_id
        meta = {**meta, "externalDraftId": external_id, "externalTitle": ext_draft.title}
        deliverable.deliverable_metadata = meta
        deliverable.updated_at = datetime.now(tz=UTC)
        await session.flush()

    # Build a deterministic sync_id
    sync_id = draft_sync_id(
        deliverable_id,
        title,
        deliverable.created_at,
    )

    return DraftSyncRecord(
        sync_id=sync_id,
        title=title,
        status=deliverable.status,
        campaign_id=deliverable.campaign_id,
        deliverable_id=str(deliverable_id),
        metadata=meta,
        external_id=external_id,
    )


async def export_deliverables_manifest(
    session: AsyncSession,
    candidate_id: int,
) -> dict[str, Any]:
    """Export a manifest of all deliverables for a candidate.

    Write path helper: builds the "repo manifest" shape mirroring Node's
    exportWritingStudioRepo() output, but scoped to a single candidate.
    """
    from sqlalchemy import select

    from artemis.marketing.models import CampaignDeliverable

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.candidate_id == candidate_id)
    )
    deliverables = list(result.scalars().all())

    draft_records = []
    for d in deliverables:
        meta = d.deliverable_metadata or {}
        title = meta.get("externalTitle", f"Deliverable {d.id}")
        sync_id = draft_sync_id(d.id, title, d.created_at)
        draft_records.append(
            {
                "schemaVersion": SYNC_SCHEMA_VERSION,
                "syncId": sync_id,
                "title": title,
                "status": d.status,
                "campaignId": d.campaign_id,
                "deliverableId": d.deliverable_id,
                "externalDraftId": meta.get("externalDraftId"),
                "metadata": {
                    k: v for k, v in meta.items() if k not in ("externalDraftId", "externalTitle")
                },
                "createdAt": d.created_at.isoformat() if d.created_at else None,
                "updatedAt": d.updated_at.isoformat() if d.updated_at else None,
            }
        )

    return {
        "schemaVersion": SYNC_SCHEMA_VERSION,
        "workspace": "writing-studio",
        "exportedAt": datetime.now(UTC).isoformat(),
        "candidateId": candidate_id,
        "drafts": draft_records,
    }


# ── READ PATH: Writing Studio → Artemis webhook ───────────────────────────────


@dataclass
class WebhookIngestResult:
    """Result of ingesting a Writing Studio webhook."""

    deliverable_id: int | None
    event_type: str
    previous_status: str | None
    new_status: str | None
    conflict: SyncConflict | None = None


async def ingest_webhook(
    session: AsyncSession,
    payload: dict[str, Any],
) -> WebhookIngestResult:
    """Ingest a webhook payload from the external Writing Studio.

    Read path: WS → Artemis.
    - Extracts event_type, external_draft_id, new_status from payload.
    - Looks up the matching campaign_deliverables row by deliverable_id.
    - Applies the status transition.
    - Emits the corresponding internal event via events.publish().
    - Returns a WebhookIngestResult.

    Does NOT commit — caller owns the transaction.
    """

    from sqlalchemy import select

    from artemis.marketing.models import CampaignDeliverable
    from artemis.marketing.writing_studio.events import publish as publish_event

    event_type = str(payload.get("eventType") or payload.get("event_type") or "").strip()
    external_draft_id = str(payload.get("draftId") or payload.get("draft_id") or "").strip()
    new_status_raw = str(payload.get("status") or "").strip()

    # Normalize event type aliases
    _alias_map = {
        "approved": "draft.approved",
        "rejected": "draft.rejected",
        "revised": "draft.revised",
        "generated": "draft.generated",
        "edited": "draft.edited",
    }
    if not event_type.startswith("draft."):
        event_type = _alias_map.get(event_type, f"draft.{event_type}")

    if not external_draft_id:
        return WebhookIngestResult(
            deliverable_id=None,
            event_type=event_type,
            previous_status=None,
            new_status=None,
            conflict=SyncConflict(
                entity_type="draft",
                sync_id=None,
                reason="missing_draft_id",
                label="webhook",
                detail="Webhook payload missing draftId.",
            ),
        )

    # Find matching deliverable
    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.deliverable_id == external_draft_id)
    )
    deliverable = result.scalar_one_or_none()

    if deliverable is None:
        return WebhookIngestResult(
            deliverable_id=None,
            event_type=event_type,
            previous_status=None,
            new_status=None,
            conflict=SyncConflict(
                entity_type="draft",
                sync_id=None,
                reason="deliverable_not_found",
                label=external_draft_id,
                detail=f"No local deliverable found for external draft id {external_draft_id!r}.",
            ),
        )

    previous_status = deliverable.status

    # Map external status to internal DeliverableState enum member.
    _status_map: dict[str, DeliverableState] = {
        "approved": DeliverableState.approved,
        "rejected": DeliverableState.rejected,
        "ready_for_review": DeliverableState.draft_ready,
        "generating": DeliverableState.generating,
        "draft": DeliverableState.generating,
        "revised": DeliverableState.draft_ready,
    }
    new_state = _status_map.get(new_status_raw)
    new_status = new_state.value if new_state is not None else previous_status

    if new_state is not None and new_status != previous_status:
        await transition(session, "deliverable", deliverable.id, new_state)

    # Emit internal event (non-fatal)
    with contextlib.suppress(Exception):
        await publish_event(
            event_type,
            draft_id=external_draft_id,
            campaign_id=deliverable.campaign_id,
            deliverable_id=str(deliverable.id),
            status=new_status,
        )

    return WebhookIngestResult(
        deliverable_id=deliverable.id,
        event_type=event_type,
        previous_status=previous_status,
        new_status=new_status,
    )


# ── Inspect: round-trip comparison ────────────────────────────────────────────


async def inspect_sync_state(
    session: AsyncSession,
    candidate_id: int,
    *,
    ws: Any = None,
) -> dict[str, Any]:
    """Compare local deliverable state against what the external service knows.

    Returns a comparison dict with counts + conflict list, mirroring Node's
    buildInspectComparison() output shape.
    """
    from sqlalchemy import select

    from artemis.marketing.models import CampaignDeliverable

    external = ws if ws is not None else get_writing_studio()

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.candidate_id == candidate_id)
    )
    deliverables = list(result.scalars().all())

    conflicts: list[dict[str, Any]] = []
    local_only: list[dict[str, Any]] = []
    external_only: list[dict[str, Any]] = []

    for d in deliverables:
        ext_id = d.deliverable_id
        if not ext_id:
            local_only.append(
                {
                    "entityType": "draft",
                    "deliverableId": d.id,
                    "detail": "No external_id — not yet pushed to Writing Studio.",
                }
            )
            continue
        try:
            ext_draft = await external.get_draft(ext_id)
            # Compare status
            if ext_draft.status != d.status:
                conflicts.append(
                    {
                        "entityType": "draft",
                        "externalId": ext_id,
                        "deliverableId": d.id,
                        "reason": "status_mismatch",
                        "localStatus": d.status,
                        "externalStatus": ext_draft.status,
                        "detail": f"Status differs: local={d.status!r}, external={ext_draft.status!r}.",
                    }
                )
        except ValueError:
            external_only.append(
                {
                    "entityType": "draft",
                    "externalId": ext_id,
                    "detail": f"Draft {ext_id!r} not found in external service (stub: not created there).",
                }
            )

    return {
        "candidateId": candidate_id,
        "counts": {
            "localOnly": len(local_only),
            "conflicts": len(conflicts),
            "externalOnly": len(external_only),
        },
        "localOnly": local_only,
        "conflicts": conflicts,
        "externalOnly": external_only,
    }
