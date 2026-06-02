"""Writing Studio router — /api/writing-studio.

Endpoints (M7 — overview + draft CRUD):
  GET  /overview                        — aggregator: drafts, folders, campaigns, rules, …
  GET  /drafts                          — paginated draft list
  GET  /drafts/{draft_id}              — draft detail (with versions + content)
  PUT  /drafts/{draft_id}              — update title/status/content/folder_id
  DELETE /drafts/{draft_id}            — soft-archive (status = 'archived')

Existing C4 stub routes (kept intact):
  POST /drafts                          — create draft from candidate
  POST /drafts/{draft_id}/submit-review — Gate-2 review
  POST /drafts/{draft_id}/events/{event_kind} — webhook from Writing Studio
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, not_found
from artemis.marketing.state_machine import LEGACY_STATUS_MAP, DeliverableState, transition
from artemis.marketing.writing_studio import events as ws_events
from artemis.marketing.writing_studio import invoke as ws_invoke
from artemis.writing_rules import repository as wr_repo

router = APIRouter(
    prefix="/api/writing-studio",
    tags=["writing-studio"],
    dependencies=[Depends(require_token)],
)

# Valid event kinds that the external Writing Studio may post back.
_VALID_EVENT_KINDS = frozenset(["approved", "rejected", "revised"])

# Map short event kind → full DRAFT_EVENT_TYPES key
_EVENT_KIND_MAP: dict[str, str] = {
    "approved": "draft.approved",
    "rejected": "draft.rejected",
    "revised": "draft.revised",
}

# campaign_deliverables.status CHECK constraint does not include 'archived'.
# Soft-delete is stored as metadata.archived = true.  The status column is
# left at its current value so the existing state machine is not disturbed.
_META_ARCHIVED_KEY = "archived"


# ── M7: Overview aggregator ───────────────────────────────────────────────────


@router.get("/overview")
async def get_overview(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Aggregator for the Writing Studio overview page.

    Composes in a single handler call (one round-trip per logical concept):
      - drafts        from campaign_deliverables (excludes archived)
      - folders       from writing_folders
      - campaigns     from campaign_candidates (id + campaign_family + status)
      - rules         from writing_rules
      - examples      from writing_examples
      - sources       from writing_sources
      - profiles      from writing_profiles
      - training_candidates  [] — no backing store yet
      - sync_config          {} — no server-side config store yet

    N+1 risk: none — each domain is a single SELECT.
    Fallback contract: any failing sub-query returns empty list/object; never 500.
    """
    # --- drafts (exclude soft-archived) ---
    try:
        deliverables = await _list_deliverables(session, include_archived=False)
        drafts = [_serialize_deliverable_as_draft(d) for d in deliverables]
    except Exception:  # noqa: BLE001
        drafts = []

    # --- folders ---
    try:
        folder_rows = await wr_repo.list_folders(session)
        folders = [_serialize_folder(f) for f in folder_rows]
    except Exception:  # noqa: BLE001
        folders = []

    # --- campaigns (id + name for filter dropdown) ---
    try:
        candidate_rows = await _list_campaigns(session)
        campaigns = [_serialize_campaign(c) for c in candidate_rows]
    except Exception:  # noqa: BLE001
        campaigns = []

    # --- rules, examples, sources, profiles ---
    try:
        rule_rows = await wr_repo.list_rules(session)
        rules = [_serialize_rule(r) for r in rule_rows]
    except Exception:  # noqa: BLE001
        rules = []

    try:
        example_rows = await wr_repo.list_examples(session)
        examples = [_serialize_example(e) for e in example_rows]
    except Exception:  # noqa: BLE001
        examples = []

    try:
        source_rows = await wr_repo.list_sources(session)
        sources = [_serialize_source(s) for s in source_rows]
    except Exception:  # noqa: BLE001
        sources = []

    try:
        profile_rows = await wr_repo.list_profiles(session)
        profiles = [_serialize_profile(p) for p in profile_rows]
    except Exception:  # noqa: BLE001
        profiles = []

    return {
        "drafts": drafts,
        "folders": folders,
        "campaigns": campaigns,
        "rules": rules,
        "examples": examples,
        "sources": sources,
        "profiles": profiles,
        "training_candidates": [],
        "sync_config": {},
    }


# ── M7: Draft list ────────────────────────────────────────────────────────────


@router.get("/drafts")
async def list_drafts(
    folder_id: int | None = Query(default=None),
    campaign_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    cursor: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Paginated list of Writing Studio drafts (excludes archived by default).

    Query params:
      folder_id   — filter by folder (metadata.folder_id)
      campaign_id — filter by campaign_id column
      status      — filter by status column
      cursor      — last seen id for cursor pagination (exclusive upper bound)
      limit       — page size (default 50, max 200)
    """
    q = select(CampaignDeliverable)
    if campaign_id is not None:
        q = q.where(CampaignDeliverable.campaign_id == campaign_id)
    if status is not None:
        q = q.where(CampaignDeliverable.status == status)
    if cursor is not None:
        q = q.where(CampaignDeliverable.id < cursor)
    q = q.order_by(CampaignDeliverable.id.desc()).limit(limit)

    result = await session.execute(q)
    deliverables = list(result.scalars())

    # Filter out soft-archived rows (archived flag stored in metadata JSONB).
    # Also apply folder_id filter post-fetch (stored in JSONB metadata).
    deliverables = [
        d
        for d in deliverables
        if not _is_archived(d) and (folder_id is None or _get_meta(d, "folder_id") == folder_id)
    ]

    drafts = [_serialize_deliverable_as_draft(d) for d in deliverables]
    next_cursor = drafts[-1]["id"] if drafts else None
    return {"drafts": drafts, "nextCursor": next_cursor}


# ── M7: Draft detail ──────────────────────────────────────────────────────────


@router.get("/drafts/{draft_id}")
async def get_draft(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Return a single draft with full metadata (versions, content, threadMessages)."""
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")
    return _serialize_deliverable_detail(deliverable)


# ── M7: Draft update (PUT) ────────────────────────────────────────────────────


@router.put("/drafts/{draft_id}")
async def update_draft(
    body: dict[str, Any],
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Update a draft: title, status, content, and/or folder_id.

    Accepted body fields (all optional):
      title       — rename the draft
      status      — transition status
      content     — store as latest version body (appended to metadata.versions)
      folder_id   — move draft to folder
      folderId    — camelCase alias for folder_id
      campaignId  — update campaign_id
      audience    — persist audience in metadata
      channel     — persist channel in metadata
      metadata    — merge extra metadata into deliverable_metadata
      changeNote  — attach a note to the appended version row
      source      — attach a source label to the appended version row
    """
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    meta: dict[str, Any] = dict(deliverable.deliverable_metadata or {})
    metadata_patch = body.get("metadata")
    if metadata_patch is not None:
        if not isinstance(metadata_patch, dict):
            raise bad_request("metadata must be an object", "invalid_metadata")
        meta.update(metadata_patch)

    if "title" in body:
        title = body["title"]
        if not isinstance(title, str) or not title.strip():
            raise bad_request("title must be a non-empty string", "invalid_title")
        meta["title"] = title.strip()

    if "folder_id" in body or "folderId" in body:
        folder_id = body["folder_id"] if "folder_id" in body else body.get("folderId")
        if folder_id is not None and not isinstance(folder_id, int):
            raise bad_request("folder_id must be an integer or null", "invalid_folder_id")
        meta["folder_id"] = folder_id

    if "campaignId" in body:
        campaign_id = body["campaignId"]
        if campaign_id is not None and not isinstance(campaign_id, str):
            raise bad_request("campaignId must be a string or null", "invalid_campaign_id")
        deliverable.campaign_id = campaign_id

    if "audience" in body:
        audience = body["audience"]
        if audience is not None and not isinstance(audience, str):
            raise bad_request("audience must be a string or null", "invalid_audience")
        meta["audience"] = audience

    if "channel" in body:
        channel = body["channel"]
        if channel is not None and not isinstance(channel, str):
            raise bad_request("channel must be a string or null", "invalid_channel")
        meta["channel"] = channel

    if "status" in body:
        status_str = str(body["status"])
        target_state = LEGACY_STATUS_MAP.get(("deliverable", status_str))
        if target_state is None:
            target_state = DeliverableState(status_str)
        await transition(
            session,
            "deliverable",
            deliverable.id,
            target_state,
            actor="writing_studio_api",
            reason="writing_studio_put_draft",
        )

    if "content" in body:
        content_val = body["content"]
        if not isinstance(content_val, str):
            raise bad_request("content must be a string", "invalid_content")
        versions: list[dict[str, Any]] = list(meta.get("versions", []))
        new_version: dict[str, Any] = {
            "id": f"v{len(versions) + 1}",
            "version_number": len(versions) + 1,
            "content": content_val,
            "created_at": datetime.now(UTC).isoformat(),
        }
        change_note = body.get("changeNote")
        if change_note is not None:
            if not isinstance(change_note, str):
                raise bad_request("changeNote must be a string", "invalid_change_note")
            new_version["change_note"] = change_note
        source = body.get("source")
        if source is not None:
            if not isinstance(source, str):
                raise bad_request("source must be a string", "invalid_version_source")
            new_version["source"] = source
        if metadata_patch is not None:
            new_version["metadata"] = metadata_patch
        versions.insert(0, new_version)
        meta["versions"] = versions

    deliverable.deliverable_metadata = meta
    deliverable.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(deliverable)
    return _serialize_deliverable_detail(deliverable)


# ── M7: Draft delete (soft-archive) ──────────────────────────────────────────


@router.delete("/drafts/{draft_id}")
async def delete_draft(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Soft-archive a draft: sets status = 'archived'.

    The DB row is NOT deleted (lossless memory rule). The draft disappears from
    the default overview/list which excludes archived entries.
    """
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    meta: dict[str, Any] = dict(deliverable.deliverable_metadata or {})
    meta[_META_ARCHIVED_KEY] = True
    deliverable.deliverable_metadata = meta
    deliverable.updated_at = datetime.now(UTC)
    await session.commit()
    return {"ok": True, "id": draft_id, "archived": True}


# ── C4: Existing stub routes (kept intact) ────────────────────────────────────


@router.post("/drafts", status_code=201)
async def create_draft(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a Writing Studio draft from a campaign candidate.

    Body:
      candidate_id: int          (required)
      asset_context: dict | None (optional — pre-assembled asset bundle)
    """
    candidate_id = body.get("candidate_id")
    if candidate_id is None:
        raise bad_request("candidate_id is required", "missing_candidate_id")
    try:
        candidate_id = int(candidate_id)
    except (TypeError, ValueError):
        raise bad_request("candidate_id must be an integer", "invalid_candidate_id")  # noqa: B904

    asset_context = body.get("asset_context")
    asset_context_bundle: list[dict[str, Any]] | None = None
    if asset_context is not None:
        if isinstance(asset_context, list):
            asset_context_bundle = asset_context
        elif isinstance(asset_context, dict):
            asset_context_bundle = [asset_context]

    try:
        draft = await ws_invoke.create_draft_from_candidate(
            session,
            candidate_id=candidate_id,
            asset_context_bundle=asset_context_bundle,
        )
    except ValueError as exc:
        raise not_found(str(exc), "candidate_not_found")  # noqa: B904

    return _serialize_draft(draft)


@router.post("/drafts/{draft_id}/submit-review", status_code=200)
async def submit_review(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Submit a draft for Gate-2 review."""
    try:
        record = await ws_invoke.submit_draft_for_review(session, deliverable_id=draft_id)
    except ValueError as exc:
        raise not_found(str(exc), "draft_not_found")  # noqa: B904

    return _serialize_approval(record)


@router.post("/drafts/{draft_id}/events/{event_kind}", status_code=200)
async def post_draft_event(
    draft_id: str = Path(...),
    event_kind: str = Path(...),
) -> dict[str, Any]:
    """Receive a lifecycle event webhook from the external Writing Studio."""
    if event_kind not in _VALID_EVENT_KINDS:
        raise bad_request(
            f"Invalid event_kind {event_kind!r}. Valid: {sorted(_VALID_EVENT_KINDS)}",
            "invalid_event_kind",
        )

    event_type = _EVENT_KIND_MAP[event_kind]
    event = await ws_events.publish(event_type, draft_id=draft_id)

    return {
        "ok": True,
        "eventKind": event_kind,
        "draftId": draft_id,
        "eventId": event.event_id if event else None,
    }


# ── Internal helpers ──────────────────────────────────────────────────────────


async def _list_deliverables(
    session: AsyncSession, *, include_archived: bool = False
) -> list[CampaignDeliverable]:
    q = select(CampaignDeliverable).order_by(CampaignDeliverable.updated_at.desc())
    result = await session.execute(q)
    rows = list(result.scalars())
    if not include_archived:
        rows = [r for r in rows if not _is_archived(r)]
    return rows


async def _list_campaigns(session: AsyncSession) -> list[CampaignCandidate]:
    """Return all campaign candidates for the filter dropdown (minimal subset)."""
    result = await session.execute(
        select(CampaignCandidate).order_by(CampaignCandidate.id.desc()).limit(200)
    )
    return list(result.scalars())


def _is_archived(deliverable: CampaignDeliverable) -> bool:
    """Return True if the deliverable has been soft-archived via metadata flag."""
    meta = deliverable.deliverable_metadata
    if isinstance(meta, dict):
        return bool(meta.get(_META_ARCHIVED_KEY, False))
    return False


def _get_meta(deliverable: CampaignDeliverable, key: str, default: Any = None) -> Any:
    meta = deliverable.deliverable_metadata
    if isinstance(meta, dict):
        return meta.get(key, default)
    return default


def _serialize_deliverable_as_draft(d: CampaignDeliverable) -> dict[str, Any]:
    """Serialize a CampaignDeliverable for the draft list / overview.

    Frontend reads: id, title, status, asset_type, campaign_id, folder_id,
    folder_name, updated_at, metadata.
    """
    meta = d.deliverable_metadata if isinstance(d.deliverable_metadata, dict) else {}
    title: str = meta.get("title") or meta.get("externalTitle") or f"Draft {d.id}"
    return {
        "id": d.id,
        "title": title,
        "status": d.status,
        "asset_type": meta.get("asset_type") or meta.get("assetType"),
        "campaign_id": d.campaign_id,
        "folder_id": meta.get("folder_id"),
        "folder_name": meta.get("folder_name"),
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "metadata": meta,
    }


def _serialize_deliverable_detail(d: CampaignDeliverable) -> dict[str, Any]:
    """Serialize a deliverable for the detail view.

    Adds: versions (top-level array), content (latest version body),
    threadMessages ([] — not stored separately yet).
    """
    base = _serialize_deliverable_as_draft(d)
    meta = d.deliverable_metadata if isinstance(d.deliverable_metadata, dict) else {}
    versions: list[Any] = meta.get("versions", [])
    content: str = versions[0]["content"] if versions and isinstance(versions[0], dict) else ""
    base["versions"] = versions
    base["content"] = content
    base["threadMessages"] = []
    return base


def _serialize_folder(f: Any) -> dict[str, Any]:
    """Serialize a WritingFolder for the overview.

    Frontend reads: id, name, parent_folder_id, campaign_id.
    (draftCount is computed client-side from drafts array — not precomputed here.)
    """
    return {
        "id": f.id,
        "name": f.name,
        "parent_folder_id": f.parent_folder_id,
        "campaign_id": f.campaign_id,
        "description": f.description,
        "sync_id": f.sync_id,
    }


def _serialize_campaign(c: CampaignCandidate) -> dict[str, Any]:
    """Serialize a CampaignCandidate for the campaign filter dropdown."""
    return {
        "id": c.id,
        "name": c.campaign_family,
        "status": c.decision_state,
    }


def _serialize_rule(r: Any) -> dict[str, Any]:
    return {
        "id": r.id,
        "profile_id": r.profile_id,
        "rule_type": r.rule_type,
        "title": r.title,
        "body": r.body,
        "status": r.status,
    }


def _serialize_example(e: Any) -> dict[str, Any]:
    return {
        "id": e.id,
        "profile_id": e.profile_id,
        "title": e.title,
        "body": e.body,
        "example_type": e.example_type,
        "asset_type": e.asset_type,
        "channel": e.channel,
    }


def _serialize_source(s: Any) -> dict[str, Any]:
    return {
        "id": s.id,
        "profile_id": s.profile_id,
        "source_key": s.source_key,
        "title": s.title,
        "source_type": s.source_type,
        "file_name": s.file_name,
        "normalized_content": s.normalized_content,
        "original_content": s.original_content,
    }


def _serialize_profile(p: Any) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "status": p.status,
        "default_model_provider": p.default_model_provider,
        "default_model_id": p.default_model_id,
    }


# ── C4 serializers (kept intact) ─────────────────────────────────────────────


def _serialize_draft(draft: ws_invoke.Draft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "externalId": draft.external_id,
        "candidateId": draft.candidate_id,
        "title": draft.title,
        "status": draft.status,
        "briefText": draft.brief_text,
        "assetContextBundle": draft.asset_context_bundle,
        "metadata": draft.metadata,
        "createdAt": draft.created_at.isoformat() if draft.created_at else None,
    }


def _serialize_approval(record: ws_invoke.ApprovalRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "kind": record.kind,
        "subjectId": record.subject_id,
        "status": record.status,
        "externalApprovalId": record.external_approval_id,
        "createdAt": record.created_at.isoformat() if record.created_at else None,
    }
