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

import logging
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
from artemis.marketing.writing_studio.compose_engine import (
    build_writing_memory_prompt,
    extract_proposed_learnings,
)
from artemis.writing_rules import repository as wr_repo
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

    rules = await wr_repo.list_rules(session)
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

    t0 = datetime.now(UTC)
    result = await run_turn(
        adapter=adapter,
        messages=messages,
        system=prompt["systemPrompt"],
        model=model_id,
        max_iterations=1,  # writing turns are single-shot
    )
    duration_ms = int((datetime.now(UTC) - t0).total_seconds() * 1000)

    # Extract response text from the last assistant message.
    response_text = ""
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    response_text += block.text
            break

    if not response_text.strip():
        raise bad_request("Model returned no text", "compose_empty_response")

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
    persisted_assistant = await wr_repo.create_thread_message(
        session,
        draft_id=draft_id,
        role="assistant",
        content=response_text,
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
        "responseText": response_text,
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
    content: str = versions[0]["content"] if versions and isinstance(versions[0], dict) else ""
    base["versions"] = versions
    base["content"] = content
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
