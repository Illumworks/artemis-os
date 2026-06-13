"""Writing Studio router — /api/writing-studio.

Endpoints (M7 — overview + draft CRUD):
  GET  /overview                        — aggregator: drafts, folders, campaigns, rules, …
  GET  /drafts                          — paginated draft list
  GET  /drafts/{draft_id}              — draft detail (with versions + content)
  PUT  /drafts/{draft_id}              — update title/status/content/folder_id
  DELETE /drafts/{draft_id}            — soft-archive (status = 'archived')

Phase 2 piece ③ — compose engine:
  POST /drafts/{draft_id}/compose      — converse with the AI about a draft

Phase 3 Piece B — writing learning loop:
  GET  /training-candidates            — list candidates (optional ?status=)
  POST /training-candidates            — manually propose a candidate
  POST /training-candidates/{id}/decision — approve or reject a candidate

Existing C4 stub routes (kept intact):
  POST /drafts                          — create draft from candidate
  POST /drafts/{draft_id}/submit-review — Gate-2 review
  POST /drafts/{draft_id}/events/{event_kind} — webhook from Writing Studio
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.identity.dependencies import get_current_user
from artemis.identity.models import User
from artemis.marketing.models import CampaignCandidate, CampaignDeliverable
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, internal, not_found
from artemis.marketing.state_machine import LEGACY_STATUS_MAP, DeliverableState, transition
from artemis.marketing.writing_studio import events as ws_events
from artemis.marketing.writing_studio import invoke as ws_invoke
from artemis.marketing.writing_studio import review_notifications
from artemis.marketing.writing_studio.collab.routes import broadcast_version_rebase
from artemis.marketing.writing_studio.compose_engine import (
    _latest_draft_content,
    build_writing_memory_prompt,
    extract_proposed_learnings,
    parse_draft_fence,
    strip_proposed_learning_lines,
)
from artemis.marketing.writing_studio.live_content import apply_live_content
from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules import tag_registry_repository as tag_repo
from artemis.writing_rules.seed_corpus import import_writing_seed_corpus

_logger = logging.getLogger(__name__)

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

_MISSING = object()

# campaign_deliverables.status CHECK constraint does not include 'archived'.
# Soft-delete is stored as metadata.archived = true.  The status column is
# left at its current value so the existing state machine is not disturbed.
_META_ARCHIVED_KEY = "archived"
_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


class ReadyForReviewRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reviewer_email_camel: str | None = Field(default=None, alias="reviewerEmail")
    reviewer_email: str | None = None


def _build_tag_registry_options(
    dimensions: list[Any],
    values: list[Any],
) -> dict[str, list[str]]:
    """Return active dimension -> allowed values, preserving registry order."""
    registry: dict[str, list[str]] = {dimension.key: [] for dimension in dimensions}
    seen: dict[str, set[str]] = {dimension.key: set() for dimension in dimensions}
    for row in values:
        allowed = registry.get(row.dimension_key)
        if allowed is None:
            continue
        if row.value in seen[row.dimension_key]:
            continue
        seen[row.dimension_key].add(row.value)
        allowed.append(row.value)
    return {dimension: allowed for dimension, allowed in registry.items() if allowed}


def _strip_json_fences(text: str) -> str:
    stripped = text.strip()
    match = _JSON_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _parse_suggested_tags_json(text: str) -> dict[str, Any]:
    candidate = _strip_json_fences(text)
    if not candidate:
        return {}

    payloads = [candidate]
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end != -1 and start < end:
        payloads.append(candidate[start : end + 1])

    for payload in payloads:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _validate_suggested_tags(
    suggestions: dict[str, Any],
    allowed_by_dimension: dict[str, set[str]],
) -> dict[str, str]:
    validated: dict[str, str] = {}
    for raw_dimension, raw_value in suggestions.items():
        if not isinstance(raw_dimension, str):
            continue
        dimension = raw_dimension.strip()
        if not dimension:
            continue
        if dimension not in allowed_by_dimension:
            _logger.debug("Dropping suggested tag with unknown dimension '%s'", raw_dimension)
            continue
        if not isinstance(raw_value, str):
            _logger.debug("Dropping suggested tag '%s' with non-string value", dimension)
            continue
        value = raw_value.strip()
        if not value:
            continue
        if value not in allowed_by_dimension[dimension]:
            _logger.debug(
                "Dropping suggested tag '%s' with unknown value '%s'",
                dimension,
                raw_value,
            )
            continue
        validated[dimension] = value
    return validated


def _build_tag_suggestion_prompt(
    *,
    registry_options: dict[str, list[str]],
    draft_text: str,
) -> tuple[str, str]:
    system_prompt = (
        "You suggest Writing Studio structured tags from a locked registry. "
        "For each dimension, choose the single best-fit value from its allowed list, "
        "or omit the dimension if the text does not clearly indicate one. "
        'Reply with JSON only in the shape {"dimension_key": "value"}. '
        "Use ONLY the listed dimensions and ONLY the listed values."
    )
    user_prompt = (
        "Allowed registry values by dimension:\n"
        f"{json.dumps(registry_options, indent=2, sort_keys=True)}\n\n"
        "Draft text:\n"
        f"{draft_text}"
    )
    return system_prompt, user_prompt


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
    await _repair_legacy_folder_assignments(session)

    # --- drafts (exclude soft-archived) ---
    try:
        deliverables = await _list_deliverables(session, include_archived=False)
        drafts = [_serialize_deliverable_as_draft(d) for d in deliverables]
    except Exception:  # noqa: BLE001
        drafts = []

    # --- campaigns (id + name for filter dropdown) — fetched BEFORE folders so
    # that we can derive live folder names from the candidate name. ---
    try:
        candidate_rows = await _list_campaigns(session)
        campaigns = [_serialize_campaign(c) for c in candidate_rows]
    except Exception:  # noqa: BLE001
        candidate_rows = []
        campaigns = []

    # Build a lookup from str(candidate_id) -> candidate for folder name derivation.
    _candidate_name_by_str_id: dict[str, str] = {
        str(c.id): (c.name or c.campaign_family or f"Campaign {c.id}") for c in candidate_rows
    }

    # --- folders ---
    try:
        folder_rows = await wr_repo.list_folders(session)
        folders = [_serialize_folder(f, _candidate_name_by_str_id) for f in folder_rows]
    except Exception:  # noqa: BLE001
        folders = []

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

    try:
        active_profile_row = await wr_repo.get_active_profile(session)
        active_profile = _serialize_profile(active_profile_row) if active_profile_row else None
    except Exception:  # noqa: BLE001
        active_profile = None

    # --- training candidates (all statuses — frontend filters by status itself) ---
    try:
        tc_rows = await wr_repo.list_training_candidates(session)
        training_candidates = [_serialize_training_candidate(c) for c in tc_rows]
    except Exception:  # noqa: BLE001
        training_candidates = []

    return {
        "drafts": drafts,
        "folders": folders,
        "campaigns": campaigns,
        "rules": rules,
        "examples": examples,
        "sources": sources,
        "profiles": profiles,
        "activeProfile": active_profile,
        "active_profile": active_profile,
        "trainingCandidates": training_candidates,
        "training_candidates": training_candidates,
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
    thread_messages = await wr_repo.list_thread_messages_for_draft(session, draft_id)
    return _serialize_deliverable_detail(deliverable, thread_messages)


@router.get("/drafts/{draft_id}/tags")
async def get_draft_tags(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str | list[str]]:
    """Return the draft's structured tags map (or {} when untagged)."""
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")
    return wr_repo.get_structured_tags_from_metadata(deliverable.deliverable_metadata)


@router.post("/drafts/{draft_id}/tags/suggest")
async def suggest_draft_tags(
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, dict[str, str]]:
    """Suggest registry-backed structured tags for a draft without persisting."""
    from artemis.agent import run_turn
    from artemis.agent import user_message as make_user_msg
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    draft_text = _latest_draft_content(deliverable).strip()
    if not draft_text:
        return {"suggestions": {}}

    dimensions = await tag_repo.list_tag_dimensions(session)
    values = await tag_repo.list_tag_values(session)
    registry_options = _build_tag_registry_options(dimensions, values)
    if not registry_options:
        return {"suggestions": {}}

    profile = await wr_repo.get_active_profile(session)
    system_prompt, user_prompt = _build_tag_suggestion_prompt(
        registry_options=registry_options,
        draft_text=draft_text,
    )

    try:
        adapter = resolve_adapter(
            getattr(profile, "default_model_provider", None) or None,
        )
    except NoProviderAvailableError as exc:
        raise bad_request(
            "No LLM provider is available. Add an API key in Integrations.",
            "no_provider",
        ) from exc

    result = await run_turn(
        adapter=adapter,
        messages=[make_user_msg(user_prompt)],
        system=system_prompt,
        model=getattr(profile, "default_model_id", None) or None,
        max_iterations=1,
    )

    response_text = ""
    for msg in reversed(result.messages):
        if msg.role != "assistant":
            continue
        for block in msg.content:
            block_text = getattr(block, "text", None)
            if isinstance(block_text, str):
                response_text += block_text
        if response_text:
            break

    parsed = _parse_suggested_tags_json(response_text)
    allowed_by_dimension = {
        dimension: set(allowed_values) for dimension, allowed_values in registry_options.items()
    }
    return {"suggestions": _validate_suggested_tags(parsed, allowed_by_dimension)}


@router.put("/drafts/{draft_id}/tags")
async def put_draft_tags(
    body: dict[str, Any],
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, str | list[str]]:
    """Validate and persist structured tags in deliverable_metadata.structured_tags."""
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")
    if "tags" not in body:
        raise bad_request("tags is required", "draft_missing_tags")  # noqa: B904

    try:
        structured_tags = await tag_repo.validate_structured_tags(session, body.get("tags"))
    except tag_repo.TagRegistryValidationError as exc:
        raise bad_request(str(exc), "draft_invalid_tags") from exc  # noqa: B904

    meta = dict(deliverable.deliverable_metadata or {})
    meta["structured_tags"] = structured_tags
    deliverable.deliverable_metadata = meta
    deliverable.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(deliverable)
    return wr_repo.get_structured_tags_from_metadata(deliverable.deliverable_metadata)


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
    deliverable = await session.get(CampaignDeliverable, draft_id, with_for_update=True)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    meta: dict[str, Any] = dict(deliverable.deliverable_metadata or {})
    committed_content: str | None = None  # set when an explicit version is minted
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
        committed_content = content_val
        # An explicit version is now the authoritative latest body — clear any
        # transient autosave buffer so it doesn't shadow the saved version.
        meta.pop("live_content", None)
        meta.pop("live_content_updated_at", None)
        # Bump the version counter so any in-flight autosave built on the old
        # base is rejected (the live_content is gone — stale writes would
        # re-introduce it over the newly-saved version).
        meta["live_content_version"] = int(meta.get("live_content_version", 0)) + 1

    # Composer Stage 1: lossless autosave. liveContent persists the current
    # editor body WITHOUT minting a new version row. The serializer +
    # compose engine prefer live_content over versions[0].content when set,
    # so the user always sees their latest typing, but version history stays
    # clean (only Save-version creates rows).
    #
    # Phase 2 soft-lock: compare-and-set on live_content_version.  If the
    # client sends baseVersion and it doesn't match the current counter, the
    # write is rejected with 409 so the late writer is warned instead of
    # silently clobbering the other editor's content.
    if "liveContent" in body:
        current_version = int(meta.get("live_content_version", 0))
        base_version = body.get("baseVersion")
        if (
            base_version is not None
            and isinstance(base_version, int)
            and base_version != current_version
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Draft changed since you last loaded it — your latest edit was not saved.",
                    "code": "stale_live_content",
                    "currentVersion": current_version,
                    "liveContent": meta.get("live_content"),
                    "liveContentUpdatedAt": meta.get("live_content_updated_at"),
                },
            )
        live_val = body["liveContent"]
        if live_val is None:
            meta.pop("live_content", None)
            meta.pop("live_content_updated_at", None)
            meta["live_content_version"] = current_version + 1
        else:
            if not isinstance(live_val, str):
                raise bad_request("liveContent must be a string or null", "invalid_live_content")
            # Single mutation site shared with the collab room flush.
            apply_live_content(meta, live_val)

    deliverable.deliverable_metadata = meta
    deliverable.updated_at = datetime.now(UTC)
    await session.commit()

    # Phase 3 collab coexistence: when an explicit version is committed (which
    # clears live_content), snap any live co-editors to the saved version so the
    # room doesn't re-flush stale steps over it (R12). No-op if no room is live.
    if committed_content is not None:
        await broadcast_version_rebase(draft_id, committed_content)

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


# ── Phase 2 piece ③: Compose endpoint ────────────────────────────────────────


@router.post("/drafts/{draft_id}/compose", status_code=200)
async def compose_draft(
    body: dict[str, Any],
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Converse with the AI about a draft.

    Body fields (all optional):
      request      — the user's message / writing action
      selectedText — selected passage in the editor (if any)
      attachments  — list of {name, type, text} source excerpts

    Behaviour (port of Node compose route at writing-studio.js:524):
      1. Load the draft (CampaignDeliverable) — 404 if not found.
      2. Load the active writing profile (from deliverable_metadata.voiceProfileSlug
         or first active profile), then all rules + examples.
      3. Load prior thread messages for conversation context.
      4. Build the system + user prompt via build_writing_memory_prompt
         (rules injected, anti-fabrication guardrail included).
      5. Invoke the model via the provider cascade (resolve_adapter).
      6. Persist: user message + assistant response via create_thread_message.
      7. Extract "Proposed learning:" lines from the response.
      8. Return: responseText, proposedCandidates, persistedMessages, trace.

    NOTE on proposedCandidates (Phase 3 Piece B): they are now PERSISTED to the
    writing_training_candidates table at status "proposed" and returned with real
    ids; the review modal approve/reject loop promotes an approved candidate into
    writing_rules/examples. If persistence fails the response still returns an
    in-memory candidate shape so the UI badge renders.
    """
    import logging
    from datetime import UTC, datetime

    from artemis.agent import run_turn
    from artemis.agent import user_message as make_user_msg
    from artemis.agent.types import Message, TextBlock
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    _logger = logging.getLogger(__name__)

    # ── 1. Load draft ──────────────────────────────────────────────────────────
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    meta: dict[str, Any] = deliverable.deliverable_metadata or {}

    # ── 2. Resolve profile, rules, examples ──────────────────────────────────
    # Try to honour voiceProfileSlug stored in deliverable_metadata.
    voice_profile_slug: str | None = meta.get("voiceProfileSlug")
    profile = None
    if voice_profile_slug:
        profiles = await wr_repo.list_profiles(session)
        for p in profiles:
            if (p.name or "").lower().replace(" ", "-") == voice_profile_slug.lower():
                profile = p
                break
    if profile is None:
        profile = await wr_repo.get_active_profile(session)

    all_rules = await wr_repo.list_rules(session)
    structured_tags = wr_repo.get_structured_tags_from_metadata(meta)
    rules = await wr_repo.resolve_grounding_rules(
        session,
        profile_id=getattr(profile, "id", None),
        fallback_rules=all_rules,
        structured_tags=structured_tags,
    )
    examples = await wr_repo.list_examples(session)

    # ── 3. Load prior thread messages ─────────────────────────────────────────
    prior_messages = await wr_repo.list_thread_messages_for_draft(session, draft_id)

    # ── 4. Build prompt ────────────────────────────────────────────────────────
    request_text: str | None = body.get("request") if isinstance(body.get("request"), str) else None
    selected_text: str | None = (
        body.get("selectedText") if isinstance(body.get("selectedText"), str) else None
    )
    raw_attachments = body.get("attachments")
    attachments: list[dict[str, Any]] = raw_attachments if isinstance(raw_attachments, list) else []

    prompt = build_writing_memory_prompt(
        draft=deliverable,
        profile=profile,
        rules=rules,
        examples=examples,
        request=request_text,
        selected_text=selected_text,
        attachments=attachments,
        prior_messages=prior_messages,
    )

    # ── 5. Model invocation via provider cascade ───────────────────────────────
    # Resolve adapter the same way pipelines/routes.py does it.
    try:
        adapter = resolve_adapter(
            getattr(profile, "default_model_provider", None) or None,
        )
    except NoProviderAvailableError as exc:
        raise bad_request(
            "No LLM provider is available. Add an API key in Integrations.",
            "no_provider",
        ) from exc

    # Build message list: prior conversation turns + current user turn.
    messages: list[Message] = []
    for turn in prompt["priorMessages"]:
        messages.append(
            Message(
                role=turn["role"],
                content=[TextBlock(text=turn["content"])],
            )
        )
    messages.append(make_user_msg(prompt["userPrompt"]))

    model_id: str | None = getattr(profile, "default_model_id", None) or None

    # ── Model call with one automatic retry on transient failure ──────────────
    # compose_draft uses run_turn (single-shot, max_iterations=1).  The adapter
    # raises ProviderAPIError / ClaudeCodeTimeoutError when the CLI signals
    # is_error=true, returns an empty result, or times out.  We attempt one
    # retry before surfacing the failure to the caller.  A truncated/errored
    # compose MUST NOT persist — we guard below before any DB writes.
    from artemis.providers.errors import ClaudeCodeTimeoutError, ProviderAPIError

    _run_error: Exception | None = None
    result = None
    t0 = datetime.now(UTC)

    for _attempt in range(2):  # up to 2 attempts (initial + one retry)
        _run_error = None
        try:
            t0 = datetime.now(UTC)
            result = await run_turn(
                adapter=adapter,
                messages=messages,
                system=prompt["systemPrompt"],
                model=model_id,
                max_iterations=1,  # writing turns are single-shot
            )
            break  # success — exit retry loop
        except (ProviderAPIError, ClaudeCodeTimeoutError) as exc:
            _run_error = exc
            _logger.warning(
                "compose_draft: model call failed on attempt %d for draft_id=%s: %s",
                _attempt + 1,
                draft_id,
                exc,
            )
            # continue to retry (or exhaust attempts)

    if _run_error is not None:
        # Both attempts failed — surface 503 so the FE can retry.  Do NOT persist.
        raise internal(
            f"Compose failed: {_run_error}",
            "compose_provider_error",
        )

    assert result is not None  # mypy: unreachable if _run_error is set

    duration_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)

    # ── Completeness guard — do NOT persist a truncated/abnormal result ────────
    # stop_reason != "end_turn" means the model loop ended for reasons other than
    # a clean finish (e.g. max_iterations, max_tokens).  Treat as incomplete.
    if result.stop_reason != "end_turn":
        _logger.warning(
            "compose_draft: abnormal stop_reason=%r for draft_id=%s — not persisting",
            result.stop_reason,
            draft_id,
        )
        raise internal(
            f"Compose ended abnormally (stop_reason={result.stop_reason!r}); please retry.",
            "compose_incomplete",
        )

    # Record cost — campaign-tied (writing_studio_compose). Phase 1 missed this
    # site; content drafting bypasses run_agent and calls run_turn directly, so
    # the executor instrumentation never fires for it. Tag with the deliverable's
    # candidate_id so it rolls up into the campaign cost. Never propagate.
    try:
        from artemis.costs.events import adapter_identity, record_cost_event

        _provider, _adapter_model, _path = adapter_identity(adapter)
        # Prefer the explicit model the caller chose, fall back to adapter default
        _cost_model = model_id or _adapter_model
        await record_cost_event(
            session,
            provider=_provider,
            model=_cost_model,
            provider_path=_path,
            feature_tag="writing_studio_compose",
            input_tokens=getattr(result.usage, "input_tokens", 0) if result.usage else 0,
            output_tokens=getattr(result.usage, "output_tokens", 0) if result.usage else 0,
            cache_creation_input_tokens=getattr(result.usage, "cache_creation_input_tokens", 0)
            if result.usage
            else 0,
            cache_read_input_tokens=getattr(result.usage, "cache_read_input_tokens", 0)
            if result.usage
            else 0,
            campaign_candidate_id=deliverable.candidate_id,
            duration_ms=duration_ms,
        )
    except Exception:
        _logger.warning(
            "cost_event recording failed in writing_studio compose draft_id=%s",
            draft_id,
            exc_info=True,
        )

    # Extract response text from the last assistant message.
    response_text = ""
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    response_text += block.text
            break

    # Guard against suspiciously empty text even after a clean stop_reason.
    # The adapter now raises before returning empty, but this is a belt-and-
    # suspenders check in case the text path somehow produces whitespace-only.
    if not response_text.strip():
        raise internal("Compose returned empty text; please retry.", "compose_empty_response")

    cleaned_response_text = strip_proposed_learning_lines(response_text)

    # ── Parse deliverable fence ────────────────────────────────────────────────
    # chat_message = conversational part (fence stripped); draft_copy = copy
    # inside the fence, or None.  Both are derived from the cleaned text so
    # Proposed-learning lines are already removed.
    # (Note: `deliverable` is already used above as the CampaignDeliverable ORM
    # object; use `draft_copy` for the fenced text to avoid a name collision.)
    chat_message, draft_copy = parse_draft_fence(cleaned_response_text)

    # ── 6. Persist thread messages ─────────────────────────────────────────────
    user_label = request_text or (
        "Adding source files for the next pass." if attachments else "Continue shaping this draft."
    )
    persisted_user = await wr_repo.create_thread_message(
        session,
        draft_id=draft_id,
        role="user",
        content=user_label,
        label="You",
        attachments=attachments or None,
    )
    # Persist the clean conversational message (fence stripped) so chat history
    # reads naturally on reload.  The deliverable itself is written to
    # live_content when the user clicks "Apply to document" via the autosave
    # path — we do NOT persist it here as separate metadata.
    persisted_assistant = await wr_repo.create_thread_message(
        session,
        draft_id=draft_id,
        role="assistant",
        content=chat_message,
        label="Artemis",
        trace=prompt["trace"],
        prompt={
            "systemPrompt": prompt["systemPrompt"],
            "userPrompt": prompt["userPrompt"],
        },
    )

    # ── 7. Extract + persist proposed learnings ────────────────────────────────
    proposed_texts = extract_proposed_learnings(response_text)
    proposed_candidates: list[dict[str, Any]] = []
    try:
        for text in proposed_texts:
            row = await wr_repo.create_training_candidate(
                session,
                profile_id=profile.id if profile is not None else None,
                draft_id=draft_id,
                candidate_type="rule",
                proposed_text=text,
                rationale="Extracted from compose turn",
                status="proposed",
            )
            proposed_candidates.append(_serialize_training_candidate(row))
    except Exception:  # noqa: BLE001
        _logger.warning(
            "compose_draft: failed to persist training candidates for draft %d; "
            "compose response will still succeed",
            draft_id,
            exc_info=True,
        )
        # Fall back to in-memory shape (no id/created_at) so UI still shows proposals.
        proposed_candidates = [
            {
                "id": None,
                "proposed_text": text,
                "candidate_type": "rule",
                "status": "proposed",
                "draft_id": draft_id,
                "created_at": None,
            }
            for text in proposed_texts
        ]

    await session.commit()

    # ── 8. Return response ────────────────────────────────────────────────────
    return {
        # responseText: full cleaned text (backward-compatible; do not remove).
        "responseText": cleaned_response_text,
        # chatMessage: conversational part only (fence stripped). Use this for
        # display in the chat thread.
        "chatMessage": chat_message,
        # deliverable: the copy inside the ```artemis-draft fence, or null.
        # When non-null the FE renders an "Apply to document" affordance.
        "deliverable": draft_copy,
        "proposedCandidates": proposed_candidates,
        "persistedMessages": {
            "user": _serialize_thread_message(persisted_user),
            "assistant": _serialize_thread_message(persisted_assistant),
        },
        "trace": prompt["trace"],
        "metrics": {
            "durationMs": duration_ms,
            "inputTokens": result.usage.input_tokens,
            "outputTokens": result.usage.output_tokens,
        },
    }


# ── Phase 3 Piece B: Training candidate endpoints ────────────────────────────


@router.get("/training-candidates")
async def list_training_candidates_route(
    status: str | None = Query(default=None),
    profile_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """List training candidates (newest first).

    Query params:
      status     — optional filter: 'proposed' | 'approved' | 'rejected'
      profile_id — optional filter by profile
    """
    rows = await wr_repo.list_training_candidates(session, status=status, profile_id=profile_id)
    return {"training_candidates": [_serialize_training_candidate(r) for r in rows]}


@router.post("/training-candidates", status_code=201)
async def create_training_candidate_route(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Manually propose a training candidate.

    Body:
      proposedText   — required, min 10 chars
      candidateType  — optional, default 'rule'
      rationale      — optional
      profileId      — optional int
      draftId        — optional int
    """
    proposed_text = body.get("proposedText")
    if not isinstance(proposed_text, str) or len(proposed_text) < 10:
        raise bad_request(
            "proposedText is required and must be at least 10 characters",
            "invalid_proposed_text",
        )

    candidate_type: str = (
        str(body["candidateType"]) if isinstance(body.get("candidateType"), str) else "rule"
    )
    rationale: str | None = (
        str(body["rationale"]) if isinstance(body.get("rationale"), str) else None
    )
    profile_id: int | None = (
        int(body["profileId"]) if isinstance(body.get("profileId"), int) else None
    )
    draft_id: int | None = int(body["draftId"]) if isinstance(body.get("draftId"), int) else None

    row = await wr_repo.create_training_candidate(
        session,
        profile_id=profile_id,
        draft_id=draft_id,
        candidate_type=candidate_type,
        proposed_text=proposed_text,
        rationale=rationale,
        status="proposed",
    )
    await session.commit()
    await session.refresh(row)
    return _serialize_training_candidate(row)


@router.post("/training-candidates/{candidate_id}/decision", status_code=200)
async def decide_training_candidate_route(
    body: dict[str, Any],
    candidate_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Approve or reject a training candidate.

    Body:
      status — required: 'approved' | 'rejected'

    On approve, the candidate is promoted to a WritingRule or WritingExample
    depending on candidate_type. The promoted item's kind + id is returned in
    the 'promoted' field (null if rejected or promotion fails).

    404 if candidate not found.
    400 if status is not 'approved' or 'rejected'.
    """
    status_val = body.get("status")
    if status_val not in ("approved", "rejected"):
        raise bad_request(
            "status must be 'approved' or 'rejected'",
            "invalid_decision_status",
        )

    from typing import Literal, cast

    candidate = await wr_repo.decide_training_candidate(
        session,
        candidate_id,
        status=cast("Literal['approved', 'rejected']", status_val),
    )
    if candidate is None:
        raise not_found(f"Training candidate {candidate_id} not found", "candidate_not_found")

    promoted: dict[str, Any] | None = None
    if status_val == "approved":
        try:
            promoted_item = await wr_repo.promote_training_candidate(session, candidate)
            if promoted_item is not None:
                from artemis.writing_rules.models import WritingExample

                promoted = {
                    "kind": "example" if isinstance(promoted_item, WritingExample) else "rule",
                    "id": promoted_item.id,
                }
        except Exception:  # noqa: BLE001
            _logger.warning(
                "decide_training_candidate: promote failed for candidate %d",
                candidate_id,
                exc_info=True,
            )

    await session.commit()
    await session.refresh(candidate)
    return {
        **_serialize_training_candidate(candidate),
        "promoted": promoted,
    }


# ── Composer Stage 2: rewrite-span ───────────────────────────────────────────


@router.post("/drafts/{draft_id}/rewrite-span", status_code=200)
async def rewrite_span(
    body: dict[str, Any],
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Rewrite a selected text span, grounded in the draft's tag-scoped rules.

    Stage 2 of the Composer rebuild.  This endpoint powers the floating
    selection toolbar ("Rewrite · Shorten · Lengthen · Make on-brand …").

    Body (all required unless noted):
      selectedText  — the exact text span the user highlighted
      instruction   — rewrite instruction (e.g. "Shorten", "Make on-brand",
                      "Make more formal")
      fullText      — optional; the full current draft body to provide context.
                      When omitted, the stored live_content / latest version is
                      used.

    Returns:
      rewrittenText — the clean replacement span (plain-text + light markdown,
                      same format as the stored draft content — ready to swap
                      back into the ProseMirror document)
      trace         — profile / rules / examples used (for on-brand showcase)

    Design guarantees:
    - Single-shot (max_iterations=1) — no streaming, no conversation state.
    - Grounds via the SAME machinery as compose_draft:
        build_writing_memory_prompt (voice) +
        resolve_grounding_rules on the draft's structured_tags (tag-scoped) +
        full draft as context
    - Does NOT persist thread messages (span rewrites aren't chat turns).
    - Does NOT fork the compose engine.
    - Lossless: Accept is done client-side via Stage-1 autosave; this endpoint
      only returns the rewritten text.
    """
    import logging
    from datetime import UTC, datetime

    from artemis.agent import run_turn
    from artemis.agent import user_message as make_user_msg
    from artemis.agent.types import Message, TextBlock
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    _log = logging.getLogger(__name__)

    # ── 1. Load draft ─────────────────────────────────────────────────────────
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    # ── 2. Validate body ──────────────────────────────────────────────────────
    selected_text = body.get("selectedText")
    if not isinstance(selected_text, str) or not selected_text.strip():
        raise bad_request(
            "selectedText is required and must be a non-empty string", "missing_selected_text"
        )

    instruction = body.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise bad_request(
            "instruction is required and must be a non-empty string", "missing_instruction"
        )

    # fullText is accepted in the body but not used server-side: the client
    # flushes autosave before calling this endpoint, so the stored live_content
    # is already current.  We validate it to avoid silent type confusion if the
    # caller passes unexpected data, but we do not inject it into the prompt.
    full_text_body = body.get("fullText")
    if full_text_body is not None and not isinstance(full_text_body, str):
        raise bad_request("fullText must be a string when provided", "invalid_full_text")

    # ── 3. Resolve profile, rules, examples (tag-scoped) ─────────────────────
    meta: dict[str, Any] = deliverable.deliverable_metadata or {}
    voice_profile_slug: str | None = meta.get("voiceProfileSlug")
    profile = None
    if voice_profile_slug:
        profiles = await wr_repo.list_profiles(session)
        for p in profiles:
            if (p.name or "").lower().replace(" ", "-") == voice_profile_slug.lower():
                profile = p
                break
    if profile is None:
        profile = await wr_repo.get_active_profile(session)

    all_rules = await wr_repo.list_rules(session)
    structured_tags = wr_repo.get_structured_tags_from_metadata(meta)
    # resolve_grounding_rules: returns tag-scoped subset when tags are set,
    # otherwise returns fallback_rules.  This is the showcase path for
    # "Make on-brand" — an audience=superintendent draft gets superintendent rules.
    rules = await wr_repo.resolve_grounding_rules(
        session,
        profile_id=getattr(profile, "id", None),
        fallback_rules=all_rules,
        structured_tags=structured_tags,
    )
    examples = await wr_repo.list_examples(session)

    # ── 4. Build a purpose-specific span-rewrite prompt ──────────────────────
    # We reuse build_writing_memory_prompt for voice + grounding block, but
    # override the user prompt to be span-focused (return ONLY the rewritten
    # span, not a full draft response).
    from artemis.marketing.writing_studio.compose_engine import build_writing_memory_prompt

    # Construct a rewrite-specific request string.
    rewrite_request = (
        f"Rewrite the selected passage only. Instruction: {instruction.strip()}.\n"
        "Return ONLY the rewritten passage — no preamble, no commentary, no "
        "surrounding text, no 'Proposed learning:' line. The output will be inserted "
        "verbatim in place of the original passage."
    )

    prompt = build_writing_memory_prompt(
        draft=deliverable,
        profile=profile,
        rules=rules,
        examples=examples,
        request=rewrite_request,
        selected_text=selected_text,
        attachments=None,
        prior_messages=None,
        # Span rewrites return ONLY the replacement passage — never the
        # chat-presentation/deliverable-fence directive, or the ```artemis-draft```
        # markers would be inserted verbatim into the document.
        include_chat_presentation=False,
    )

    # ── 5. Model invocation ───────────────────────────────────────────────────
    try:
        adapter = resolve_adapter(
            getattr(profile, "default_model_provider", None) or None,
        )
    except NoProviderAvailableError as exc:
        raise bad_request(
            "No LLM provider is available. Add an API key in Integrations.",
            "no_provider",
        ) from exc

    messages: list[Message] = [make_user_msg(prompt["userPrompt"])]
    model_id: str | None = getattr(profile, "default_model_id", None) or None

    t0 = datetime.now(UTC)
    result = await run_turn(
        adapter=adapter,
        messages=messages,
        system=prompt["systemPrompt"],
        model=model_id,
        max_iterations=1,
    )
    duration_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)

    # Record cost for span rewrites (separate feature_tag from compose).
    try:
        from artemis.costs.events import adapter_identity, record_cost_event

        _provider, _adapter_model, _path = adapter_identity(adapter)
        _cost_model = model_id or _adapter_model
        await record_cost_event(
            session,
            provider=_provider,
            model=_cost_model,
            provider_path=_path,
            feature_tag="writing_studio_rewrite_span",
            input_tokens=getattr(result.usage, "input_tokens", 0) if result.usage else 0,
            output_tokens=getattr(result.usage, "output_tokens", 0) if result.usage else 0,
            cache_creation_input_tokens=getattr(result.usage, "cache_creation_input_tokens", 0)
            if result.usage
            else 0,
            cache_read_input_tokens=getattr(result.usage, "cache_read_input_tokens", 0)
            if result.usage
            else 0,
            campaign_candidate_id=deliverable.candidate_id,
            duration_ms=duration_ms,
        )
    except Exception:
        _log.warning(
            "cost_event recording failed in rewrite_span draft_id=%s",
            draft_id,
            exc_info=True,
        )

    # ── 6. Extract rewritten span text ────────────────────────────────────────
    rewritten_text = ""
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    rewritten_text += block.text
            break

    # Defensive: if the model wrapped the passage in an ```artemis-draft``` (or
    # bare ```) fence despite the instruction, unwrap it so the markers never
    # reach the document. Span replacement must be plain text only.
    from artemis.marketing.writing_studio.compose_engine import parse_draft_fence

    _chat, _fenced = parse_draft_fence(rewritten_text)
    if _fenced is not None:
        rewritten_text = _fenced
    rewritten_text = re.sub(r"^```[a-zA-Z-]*\n?|\n?```$", "", rewritten_text.strip()).strip()

    if not rewritten_text.strip():
        raise bad_request("Model returned no text for the span rewrite", "rewrite_empty_response")

    # Commit cost event (no other DB writes — span rewrites don't persist).
    await session.commit()

    # ── 7. Return ─────────────────────────────────────────────────────────────
    return {
        "rewrittenText": rewritten_text.strip(),
        "trace": {
            **prompt["trace"],
            "instruction": instruction,
            "selectedTextChars": len(selected_text),
            "durationMs": duration_ms,
        },
    }


# ── Stage 4: Claim-scan endpoint ─────────────────────────────────────────────


@router.post("/drafts/{draft_id}/claim-scan", status_code=200)
async def claim_scan(
    draft_id: int = Path(..., ge=1),
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Scan a draft's current text for unregistered strong claims.

    POST body (all fields optional):
      text: str  — draft text to scan; if omitted the draft's live_content or
                   latest version content is used.

    Returns:
      {
        "flags": [
          {
            "start": int,
            "end": int,
            "text": str,
            "reason": str,
            "nearestApproved": [{"id": int, "phrasing": str, "similarity": float}]
          }
        ],
        "scannedChars": int,
        "approvedClaimsCount": int,
      }

    Logic (deterministic — no LLM):
    1. Candidate detection: quantified / superlative / comparative patterns only.
    2. Suppression: token-set similarity >= SUPPRESS_THRESHOLD against approved claims.
    3. Return remaining candidates as flags with top 1-2 nearest approved claims.
    """
    from artemis.marketing.writing_studio.claim_detector import scan_draft_for_flags

    # Load the draft to get text (if not supplied in body) and dismissed claims.
    if body is None:
        body = {}
    text: str | None = body.get("text") if isinstance(body, dict) else None

    # Always load the draft so we can retrieve dismissedClaims from metadata.
    # When `text` was supplied in the body we still need the draft for dismissals
    # (silently ignore 404 — text-only scans without a real draft are used in
    # tests and the dismissedClaims list will simply be empty).
    dismissed_claims: list[str] = []
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is not None:
        _meta_for_dismissed = deliverable.deliverable_metadata or {}
        dismissed_claims = list(_meta_for_dismissed.get("dismissedClaims", []))

    if not text:
        if deliverable is None:
            raise not_found(f"Draft {draft_id} not found", "draft_not_found")
        meta = deliverable.deliverable_metadata or {}
        # Prefer live_content (unsaved edits) over versioned content.
        text = meta.get("live_content") or ""
        if not text:
            versions = meta.get("versions") or []
            if versions:
                text = versions[-1].get("content") or ""

    text = (text or "").strip()

    # Load approved claims for the active profile.
    active_profile = await wr_repo.get_active_profile(session)
    approved_claims_list: list[tuple[int, str]] = []
    if active_profile is not None:
        claims = await wr_repo.list_claims(session, active_profile.id, status="approved")
        approved_claims_list = [(c.id, c.approved_phrasing) for c in claims]

    flags = scan_draft_for_flags(text, approved_claims_list, dismissed_claims)

    return {
        "flags": [
            {
                "start": f.start,
                "end": f.end,
                "text": f.text,
                "reason": f.reason,
                "nearestApproved": [
                    {
                        "id": n.id,
                        "phrasing": n.phrasing,
                        "similarity": n.similarity,
                    }
                    for n in f.nearest_approved
                ],
            }
            for f in flags
        ],
        "scannedChars": len(text),
        "approvedClaimsCount": len(approved_claims_list),
    }


# ── Stage 4: Claim-dismiss endpoint ──────────────────────────────────────────


@router.post("/drafts/{draft_id}/claim-dismiss", status_code=200)
async def claim_dismiss(
    draft_id: int = Path(..., ge=1),
    body: dict[str, Any] | None = None,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Record a "Disregard" dismissal for a flagged claim span.

    POST body (one of the following is required):
      text: str  — the exact text of the flagged span to dismiss.
      span: str  — alias for text (either key accepted).

    Dismissals are stored ADDITIVELY in ``deliverable_metadata.dismissedClaims``
    (a JSON array of span texts).  The list grows; entries are never deleted
    (lossless rule).  The claim-scan endpoint suppresses any candidate whose
    normalised text matches a dismissed entry.

    Returns:
      { "ok": true, "dismissedClaims": [...all dismissed texts for this draft] }

    404 if the draft does not exist.
    400 if body is missing or neither `text` nor `span` is provided.
    """
    if body is None:
        body = {}

    span_text: str | None = body.get("text") or body.get("span") or None
    if not span_text or not isinstance(span_text, str) or not span_text.strip():
        raise bad_request(
            "text (or span) is required and must be a non-empty string",
            "missing_dismiss_text",
        )

    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    meta: dict[str, Any] = dict(deliverable.deliverable_metadata or {})
    existing: list[str] = list(meta.get("dismissedClaims", []))

    # Deduplicate by normalised text — don't store the same dismissal twice.
    from artemis.marketing.writing_studio.claim_detector import _normalize

    norm_new = _normalize(span_text)
    already_dismissed = any(_normalize(e) == norm_new for e in existing)
    if not already_dismissed:
        existing.append(span_text.strip())
        meta["dismissedClaims"] = existing
        deliverable.deliverable_metadata = meta
        deliverable.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(deliverable)

    return {
        "ok": True,
        "dismissedClaims": list(
            (deliverable.deliverable_metadata or {}).get("dismissedClaims", [])
        ),
    }


# ── C4: Existing stub routes (kept intact) ────────────────────────────────────


@router.post("/drafts", status_code=201)
async def create_draft(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a Writing Studio draft.

    Body:
      candidate_id: int | None   (optional — if absent a blank draft is created
                                  under the templates placeholder candidate)
      asset_context: dict | None (optional — pre-assembled asset bundle;
                                  ignored when candidate_id is absent)
      title: str | None          (optional — used for blank drafts; default "New draft")
      folder_id: int | None      (optional — file the blank draft in this folder)
    """
    candidate_id_raw = body.get("candidate_id")

    # ── Blank-draft path (no candidate) ──────────────────────────────────────
    if candidate_id_raw is None:
        title = body.get("title") or None
        folder_id_raw = body.get("folder_id")
        folder_id: int | None = None
        if folder_id_raw is not None:
            try:
                folder_id = int(folder_id_raw)
            except (TypeError, ValueError):
                raise bad_request("folder_id must be an integer", "invalid_folder_id")  # noqa: B904
        draft = await ws_invoke.create_blank_draft(
            session,
            title=title,
            folder_id=folder_id,
        )
        return _serialize_draft(draft)

    # ── Campaign-candidate path ───────────────────────────────────────────────
    try:
        candidate_id = int(candidate_id_raw)
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


@router.post("/drafts/{draft_id}/ready-for-review", status_code=200)
async def ready_for_review(
    body: ReadyForReviewRequest,
    draft_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
    current_user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, Any]:
    """Mark a draft ready for review and send a deterministic Callie ping."""
    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    reviewer_email = await review_notifications.resolve_reviewer_email(
        session,
        deliverable,
        requested_email=body.reviewer_email_camel or body.reviewer_email,
    )

    try:
        approval = await ws_invoke.submit_draft_for_review(session, deliverable_id=draft_id)
    except ValueError as exc:
        raise bad_request(str(exc), "ready_for_review_failed") from exc

    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")

    now_iso = datetime.now(UTC).isoformat()
    author_name = (current_user.name or current_user.email or "The author").strip()
    metadata = (
        dict(deliverable.deliverable_metadata)
        if isinstance(deliverable.deliverable_metadata, dict)
        else {}
    )
    metadata.update(
        {
            "ready_for_review": True,
            "review_status": "ready_for_review",
            "ready_for_review_at": now_iso,
            "reviewer_email": reviewer_email,
            "review_requested_at": now_iso,
            "review_requested_by_email": current_user.email,
            "review_requested_by_name": current_user.name,
        }
    )
    deliverable.deliverable_metadata = metadata
    await session.flush()
    await session.commit()

    ping = await review_notifications.send_callie_ready_for_review_ping(
        session,
        draft_id=draft_id,
        title=metadata.get("title") or metadata.get("externalTitle") or f"Draft {draft_id}",
        author_name=author_name,
        reviewer_email=reviewer_email,
        mode="channel_mention",
    )

    deliverable = await session.get(CampaignDeliverable, draft_id)
    if deliverable is None:
        raise not_found(f"Draft {draft_id} not found", "draft_not_found")
    metadata = (
        dict(deliverable.deliverable_metadata)
        if isinstance(deliverable.deliverable_metadata, dict)
        else {}
    )
    metadata.update(
        {
            "review_notification_sent_at": datetime.now(UTC).isoformat() if ping.ok else None,
            "review_notification_target": ping.target,
            "review_notification_slack_user_id": ping.slack_user_id,
            "review_notification_channel_id": ping.channel_id,
            "review_notification_error": ping.error,
        }
    )
    deliverable.deliverable_metadata = metadata
    await session.flush()
    thread_messages = await wr_repo.list_thread_messages_for_draft(session, draft_id)
    await session.commit()
    await session.refresh(deliverable)

    return {
        "ok": ping.ok,
        "draft": _serialize_deliverable_detail(deliverable, thread_messages),
        "approval": _serialize_approval(approval),
        "reviewerEmail": reviewer_email,
        "delivery": {
            "ok": ping.ok,
            "target": ping.target,
            "slackUserId": ping.slack_user_id,
            "channelId": ping.channel_id,
            "error": ping.error,
        },
    }


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


# ── Seed corpus import ───────────────────────────────────────────────────────


@router.post("/seed/import", status_code=200)
async def seed_import(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Import the built-in Amira writing-agent seed corpus.

    Idempotent — re-running inserts zero duplicates.

    Response fields (consumed by the frontend importWritingSeedApi):
      profileId            — int: the active profile id
      profileName          — str
      profilesInserted     — int (0 or 1)
      profilesSkipped      — int (0 or 1)
      sourcesUpserted      — int: number of writing_sources rows written
      rulesUpserted        — int: number of writing_rules rows written
      examplesUpserted     — int: number of writing_examples rows written
      profilePromptUpdated — bool: True when system_prompt was set
      imported             — list of per-file details
      skipped              — list of source-only files
    """
    result = await import_writing_seed_corpus(session)
    await session.commit()
    return result


# ── Folder CRUD ───────────────────────────────────────────────────────────────


@router.post("/folders", status_code=200)
async def create_folder(
    body: dict[str, Any],
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Create a Writing Studio folder.

    Body:
      name              — required, non-empty string
      parent_folder_id  — optional parent folder id
      parentFolderId    — camelCase alias for parent_folder_id

    Returns the new folder serialized identically to the /overview folders list.
    """
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        raise bad_request("name is required and must be a non-empty string", "invalid_name")

    create_kwargs: dict[str, Any] = {"name": name.strip()}
    parent_folder_id = _read_optional_folder_parent_id(body)
    if parent_folder_id is not _MISSING:
        await _validate_folder_parent_assignment(session, None, parent_folder_id)
        create_kwargs["parent_folder_id"] = parent_folder_id

    folder = await wr_repo.create_folder(session, **create_kwargs)
    await session.commit()
    await session.refresh(folder)
    return _serialize_folder(folder)


@router.put("/folders/{folder_id}", status_code=200)
async def update_folder(
    body: dict[str, Any],
    folder_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Rename a Writing Studio folder.

    Body:
      name              — optional non-empty string
      parent_folder_id  — optional parent folder id
      parentFolderId    — camelCase alias for parent_folder_id

    Returns the updated folder. 404 if not found.
    """
    folder = await wr_repo.get_folder(session, folder_id)
    if folder is None:
        raise not_found(f"Folder {folder_id} not found", "folder_not_found")

    update_kwargs: dict[str, Any] = {}
    if "name" in body:
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise bad_request("name must be a non-empty string", "invalid_name")
        update_kwargs["name"] = name.strip()

    parent_folder_id = _read_optional_folder_parent_id(body)
    if parent_folder_id is not _MISSING:
        await _validate_folder_parent_assignment(session, folder_id, parent_folder_id)
        update_kwargs["parent_folder_id"] = parent_folder_id

    if not update_kwargs:
        raise bad_request(
            "Provide name and/or parent_folder_id to update a folder",
            "invalid_folder_update",
        )

    folder = await wr_repo.update_folder(session, folder_id, **update_kwargs)

    await session.commit()
    await session.refresh(folder)
    return _serialize_folder(folder)


@router.delete("/folders/{folder_id}", status_code=200)
async def delete_folder(
    folder_id: int = Path(..., ge=1),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> dict[str, Any]:
    """Delete a Writing Studio folder.

    LOSSLESS: drafts are never deleted. Every draft assigned to this folder
    has its metadata.folder_id cleared so it moves to "All drafts" — matching
    the UI confirm copy "Drafts will remain available in All drafts."

    Campaign-derived folders (campaign_id IS NOT NULL) are soft-deleted
    (deleted_at stamped) so that backfill_campaign_folders does not recreate
    them on the next overview load.

    User-created folders (campaign_id IS NULL) are hard-deleted.

    Returns 404 if the folder is not found.
    """
    deleted = await wr_repo.delete_folder(session, folder_id)
    if not deleted:
        raise not_found(f"Folder {folder_id} not found", "folder_not_found")

    await session.commit()
    return {"ok": True, "id": folder_id}


# ── Internal helpers ──────────────────────────────────────────────────────────


def _read_optional_folder_parent_id(body: dict[str, Any]) -> object:
    if "parent_folder_id" not in body and "parentFolderId" not in body:
        return _MISSING
    parent_folder_id = (
        body["parent_folder_id"] if "parent_folder_id" in body else body.get("parentFolderId")
    )
    if parent_folder_id is not None and not isinstance(parent_folder_id, int):
        raise bad_request(
            "parent_folder_id must be an integer or null",
            "invalid_parent_folder_id",
        )
    return parent_folder_id


def _collect_folder_descendants(
    folder_id: int,
    children_by_parent: dict[int, list[int]],
    seen: set[int],
) -> None:
    if folder_id in seen:
        return
    seen.add(folder_id)
    for child_id in children_by_parent.get(folder_id, []):
        _collect_folder_descendants(child_id, children_by_parent, seen)


async def _validate_folder_parent_assignment(
    session: AsyncSession,
    folder_id: int | None,
    parent_folder_id: object,
) -> None:
    if parent_folder_id is _MISSING or parent_folder_id is None:
        return

    numeric_parent_id = int(parent_folder_id)
    parent_folder = await wr_repo.get_folder(session, numeric_parent_id)
    if parent_folder is None:
        raise not_found(f"Folder {numeric_parent_id} not found", "folder_not_found")

    if folder_id is None:
        return

    if numeric_parent_id == folder_id:
        raise bad_request(
            "A folder cannot be nested inside itself",
            "invalid_parent_folder_id",
        )

    folder_rows = await wr_repo.list_folders(session)
    children_by_parent: dict[int, list[int]] = {}
    for row in folder_rows:
        parent_id = row.parent_folder_id
        if parent_id is None:
            continue
        children_by_parent.setdefault(int(parent_id), []).append(int(row.id))

    descendants: set[int] = set()
    _collect_folder_descendants(folder_id, children_by_parent, descendants)
    if numeric_parent_id in descendants:
        raise bad_request(
            "A folder cannot be nested inside one of its descendants",
            "invalid_parent_folder_id",
        )


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


async def _repair_legacy_folder_assignments(session: AsyncSession) -> None:
    """Backfill legacy Writing Studio folder metadata without surfacing errors.

    Older pipeline-created deliverables can be missing ``metadata.folder_id``.
    Repairing them here keeps the live app self-healing for existing data while
    the create path below stamps the correct folder metadata for new rows.
    """
    try:
        result = await ws_invoke.backfill_campaign_folders(session)
    except Exception:  # noqa: BLE001
        _logger.exception("Writing Studio folder backfill failed during overview load")
        await session.rollback()
        return

    if result.rows_updated or result.folders_created or result.family_folders_removed:
        await session.commit()


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


def _serialize_thread_message(m: Any) -> dict[str, Any]:
    """Serialize a WritingDraftThreadMessage for the frontend chat panel."""
    return {
        "id": m.id,
        "draftId": m.draft_id,
        "role": m.role,
        "label": m.label,
        "text": m.content,
        "content": m.content,
        "attachments": m.attachments,
        "trace": m.trace,
        "engine": m.engine,
        "createdAt": m.created_at.isoformat() if m.created_at else None,
    }


def _serialize_deliverable_as_draft(d: CampaignDeliverable) -> dict[str, Any]:
    """Serialize a CampaignDeliverable for the draft list / overview.

    Frontend reads: id, title, status, asset_type, campaign_id, folder_id,
    folder_name, updated_at, metadata.
    """
    meta = d.deliverable_metadata if isinstance(d.deliverable_metadata, dict) else {}
    title: str = meta.get("title") or meta.get("externalTitle") or f"Draft {d.id}"
    ready_for_review = bool(meta.get("ready_for_review")) or (
        meta.get("review_status") == "ready_for_review"
    )
    return {
        "id": d.id,
        "title": title,
        "status": d.status,
        "readyForReview": ready_for_review,
        "readyForReviewAt": (
            meta.get("ready_for_review_at")
            or meta.get("readyForReviewAt")
            or meta.get("review_requested_at")
            or meta.get("reviewRequestedAt")
        ),
        "reviewerEmail": meta.get("reviewer_email") or meta.get("reviewerEmail"),
        "reviewRequestedAt": (
            meta.get("ready_for_review_at")
            or meta.get("readyForReviewAt")
            or meta.get("review_requested_at")
            or meta.get("reviewRequestedAt")
        ),
        "asset_type": meta.get("asset_type") or meta.get("assetType"),
        "campaign_id": d.campaign_id,
        "folder_id": meta.get("folder_id"),
        "folder_name": meta.get("folder_name"),
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
        "metadata": meta,
    }


def _serialize_deliverable_detail(
    d: CampaignDeliverable,
    thread_messages: list[Any] | None = None,
) -> dict[str, Any]:
    """Serialize a deliverable for the detail view.

    Adds: versions (top-level array), content (latest version body),
    threadMessages (list of persisted thread messages, empty list if none).

    ``thread_messages`` should be passed from the caller after querying
    list_thread_messages_for_draft; defaults to [] so existing callers that
    don't yet load messages continue to work.
    """
    base = _serialize_deliverable_as_draft(d)
    meta = d.deliverable_metadata if isinstance(d.deliverable_metadata, dict) else {}
    versions: list[Any] = meta.get("versions", [])
    # Composer Stage 1: prefer live_content (autosaved between explicit versions)
    # over versions[0].content. See compose_engine._latest_draft_content for the
    # mirror precedence in the LLM prompt path.
    live = meta.get("live_content")
    if isinstance(live, str) and live:
        content: str = live
    elif versions and isinstance(versions[0], dict):
        content = versions[0]["content"]
    else:
        content = ""
    base["versions"] = versions
    base["content"] = content
    base["liveContent"] = live if isinstance(live, str) else None
    base["liveContentUpdatedAt"] = meta.get("live_content_updated_at")
    base["liveContentVersion"] = int(meta.get("live_content_version", 0))
    base["threadMessages"] = [_serialize_thread_message(m) for m in (thread_messages or [])]
    return base


def _serialize_folder(
    f: Any,
    candidate_name_by_str_id: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Serialize a WritingFolder for the overview.

    Frontend reads: id, name, parent_folder_id, campaign_id, candidate_id.
    (draftCount is computed client-side from drafts array — not precomputed here.)

    When ``candidate_name_by_str_id`` is provided and the folder's
    ``campaign_id`` is a pure-integer string (i.e. a per-candidate folder),
    the folder's display ``name`` is derived from the live candidate name
    rather than the creation-time snapshot stored in ``writing_folders.name``.
    This ensures the folder name always reflects the current campaign name
    without requiring any rename-sync hook.
    """
    cid: str | None = f.campaign_id
    # Derive candidate_id (int) if campaign_id is a numeric string.
    candidate_id_int: int | None = None
    if cid and cid.isdigit():
        candidate_id_int = int(cid)

    # Use live candidate name when available; fall back to stored snapshot.
    if candidate_name_by_str_id is not None and cid and cid.isdigit():
        display_name: str = candidate_name_by_str_id.get(cid, f.name)
    else:
        display_name = f.name

    return {
        "id": f.id,
        "name": display_name,
        "parent_folder_id": f.parent_folder_id,
        "campaign_id": f.campaign_id,
        "candidate_id": candidate_id_int,
        "description": f.description,
        "sync_id": f.sync_id,
    }


def _serialize_campaign(c: CampaignCandidate) -> dict[str, Any]:
    """Serialize a CampaignCandidate for the campaign filter dropdown.

    ``id`` is the campaign_family string (e.g. "obc"), not the numeric
    candidate id.  The frontend filter compares draft.campaign_id against
    filters.campaignId, and both are now the family name string, so they
    match correctly.  Multiple candidates from the same family collapse to a
    single filter option via deduplication on the frontend (or are harmless
    duplicates since the value is identical).
    """
    return {
        "id": c.campaign_family,
        "name": c.campaign_family,
        "status": c.decision_state,
        "candidate_id": c.id,
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


def _serialize_training_candidate(c: Any) -> dict[str, Any]:
    """Serialize a WritingTrainingCandidate for the frontend review modal.

    The modal at writing-studio.js:1447-1457 reads:
      candidate_type, draft_title, proposed_text, status
    Additional fields: id, profile_id, draft_id, rationale, created_at, decided_at.
    draft_title is fetched from deliverable_metadata when available.
    """
    return {
        "id": c.id,
        "profile_id": c.profile_id,
        "draft_id": c.draft_id,
        "draft_title": None,  # populated on demand in the overview path
        "candidate_type": c.candidate_type,
        "proposed_text": c.proposed_text,
        "rationale": c.rationale,
        "status": c.status,
        "scope_json": c.scope_json,
        "source_version_id": c.source_version_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "decided_at": c.decided_at.isoformat() if c.decided_at else None,
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
