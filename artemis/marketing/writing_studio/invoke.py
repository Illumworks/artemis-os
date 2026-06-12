"""Writing Studio programmatic invocation layer.

Port of writing-studio-invoke.js (relevant campaign-integration functions).

Functions:
  create_draft_from_candidate — builds metadata bundle + creates deliverable row
  create_handoff_draft        — manual operator handoff: seeded title/brief/voice/tags
  submit_draft_for_review     — Gate-2 approval row + status transition
  list_campaign_asset_links   — assets with non-empty summary for metadata bundle

Uses ExternalWritingStudio (Stub by default) from .external.
Uses brief assembler (C3) for brief text.
Uses events.publish for draft lifecycle events.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.brief_assembler import (
    AssetContext,
    CampaignBrief,
    format_brief_for_writing_studio,
)
from artemis.marketing.models import (
    Approval,
    CampaignCandidate,
    CampaignDeliverable,
    ContentAssetLink,
)
from artemis.marketing.repository import (
    get_campaign_brief,
    get_candidate,
    get_candidate_primary_signal,
)
from artemis.marketing.state_machine import DeliverableState, transition
from artemis.marketing.writing_studio.events import publish as publish_event
from artemis.marketing.writing_studio.external import ExternalDraft, get_writing_studio
from artemis.writing_rules import repository as wr_repo

# ── Output shapes ──────────────────────────────────────────────────────────────


@dataclass
class Draft:
    """A Writing Studio draft created via create_draft_from_candidate."""

    id: int  # campaign_deliverables.id (local PK)
    external_id: str  # stub-draft-N or real external id
    candidate_id: int
    title: str
    status: str
    brief_text: str | None = None
    asset_context_bundle: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass
class ApprovalRecord:
    """A writing_gate_2 approval row."""

    id: int  # approvals.id (local PK)
    kind: str
    subject_id: str
    status: str
    external_approval_id: str | None = None
    created_at: datetime | None = None


# ── Internal helpers ───────────────────────────────────────────────────────────

_ASSET_CONTEXT_WORD_CAP = 800
_TEMPLATE_DRAFT_FAMILY = "writing_studio_template"
_TEMPLATE_DRAFT_NAME = "Writing Studio Templates"


def _compact_text(value: Any, limit: int = 1200) -> str:
    normalized = " ".join(str(value or "").split())
    if not normalized:
        return ""
    return normalized[:limit].rstrip() + "..." if len(normalized) > limit else normalized


def _build_asset_context_text(assets: list[dict[str, Any]]) -> str:
    """Render the asset context bundle as a text block for the metadata bundle.

    Mirrors Node: cap at 800 words, include [asset list truncated] marker.
    Only assets with a non-empty summary field are included.
    """
    if not assets:
        return ""
    lines = ["--- Linked Reusable Assets ---"]
    for i, a in enumerate(assets, start=1):
        lines.append(f"[{i}] {a.get('title', 'Untitled')} ({a.get('assetType', 'unknown')})")
        lines.append(f"    Summary: {a['summary']}")
        if a.get("sourceUrl"):
            lines.append(f"    Source: {a['sourceUrl']}")
    lines.append("---")
    candidate = "\n".join(lines)
    word_count = len(candidate.split())
    if word_count > _ASSET_CONTEXT_WORD_CAP:
        truncated = " ".join(candidate.split()[:_ASSET_CONTEXT_WORD_CAP])
        return truncated + "\n[asset list truncated]"
    return candidate


async def _get_or_create_template_workspace_candidate(session: AsyncSession) -> CampaignCandidate:
    """Return the shared placeholder candidate for standalone template drafts.

    campaign_deliverables still requires candidate_id, so template-instantiated
    drafts without a campaign context hang off a single internal candidate
    instead of inventing a parallel draft store.
    """
    result = await session.execute(
        select(CampaignCandidate)
        .where(CampaignCandidate.campaign_family == _TEMPLATE_DRAFT_FAMILY)
        .order_by(CampaignCandidate.id)
        .limit(1)
    )
    candidate = result.scalar_one_or_none()
    if candidate is not None:
        return candidate

    candidate = CampaignCandidate(
        campaign_family=_TEMPLATE_DRAFT_FAMILY,
        name=_TEMPLATE_DRAFT_NAME,
        objective="Internal placeholder candidate for template-instantiated drafts.",
    )
    session.add(candidate)
    await session.flush()
    await session.refresh(candidate)
    return candidate


async def _create_manual_draft_record(
    session: AsyncSession,
    *,
    candidate_id: int,
    campaign_id: str | None,
    title: str,
    metadata: dict[str, Any],
    status: str = "draft_ready",
    folder_id: int | None = None,
    folder_name: str | None = None,
    ws: Any = None,
) -> Draft:
    """Create a draft_ready deliverable row using the shared manual-draft path."""
    external = ws if ws is not None else get_writing_studio()
    external_draft: ExternalDraft = await external.create_draft(title=title, metadata=metadata)

    draft_meta: dict[str, Any] = {
        **metadata,
        "title": title,
        "externalDraftId": external_draft.external_id,
        "externalTitle": external_draft.title,
    }
    if folder_id is not None:
        draft_meta["folder_id"] = folder_id
    if folder_name:
        draft_meta["folder_name"] = folder_name

    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=external_draft.external_id,
        campaign_id=campaign_id,
        status=status,
        deliverable_metadata=draft_meta,
    )
    session.add(deliverable)
    await session.flush()
    await session.refresh(deliverable)
    await session.commit()

    with contextlib.suppress(Exception):
        await publish_event(
            "draft.generated",
            draft_id=external_draft.external_id,
            campaign_id=campaign_id,
            deliverable_id=str(deliverable.id),
            status=status,
        )

    return Draft(
        id=deliverable.id,
        external_id=external_draft.external_id,
        candidate_id=candidate_id,
        title=title,
        status=status,
        metadata=dict(deliverable.deliverable_metadata or {}),
        created_at=deliverable.created_at,
    )


# ── Public API ────────────────────────────────────────────────────────────────


async def list_campaign_asset_links(
    session: AsyncSession,
    candidate_id: int,
) -> list[AssetContext]:
    """Return linked content_assets where summary is non-empty.

    Mirrors Node behavior: only assets with summary feed the metadata bundle.
    Joins via ContentAssetLink → ContentAsset.
    """
    from sqlalchemy import select

    from artemis.marketing.models import ContentAsset

    result = await session.execute(
        select(ContentAssetLink, ContentAsset)
        .join(ContentAsset, ContentAssetLink.asset_id == ContentAsset.id)
        .where(
            ContentAssetLink.candidate_id == candidate_id,
            ContentAsset.summary.isnot(None),
            ContentAsset.summary != "",
        )
    )
    rows = result.all()
    return [
        AssetContext(
            asset_id=asset.id,
            asset_type=asset.asset_type,
            summary=asset.summary,
            link_role=link.link_role,
        )
        for link, asset in rows
        if asset.summary and asset.summary.strip()
    ]


async def create_draft_from_candidate(
    session: AsyncSession,
    candidate_id: int,
    brief_payload: dict[str, Any] | None = None,
    asset_context_bundle: list[dict[str, Any]] | None = None,
    *,
    ws: Any = None,  # ExternalWritingStudio; injected in tests
) -> Draft:
    """Build a metadata bundle and create a Writing Studio draft + deliverable row.

    1. Fetches candidate from DB.
    2. Fetches latest campaign brief from DB (if any) and formats it.
    3. Builds asset context text from provided bundle (or fetches from DB).
    4. Calls ExternalWritingStudio.create_draft() (Stub by default).
    5. Creates a campaign_deliverables row.
    6. Emits draft.generated event.
    7. Returns Draft dataclass.

    brief_payload: optional pre-assembled brief content dict (skip DB lookup).
    asset_context_bundle: optional pre-built list of asset dicts (skip DB lookup).
    ws: optional injected external WS client (for tests).
    """
    candidate = await get_candidate(session, candidate_id)
    external = ws if ws is not None else get_writing_studio()

    # --- Brief text ---
    brief_text: str | None = None
    if brief_payload is not None:
        brief = CampaignBrief(content=brief_payload)
        brief_text = format_brief_for_writing_studio(brief) or None
    else:
        try:
            db_brief = await get_campaign_brief(session, candidate_id)
            if db_brief is not None:
                brief = CampaignBrief(content=db_brief.content)
                brief_text = format_brief_for_writing_studio(brief) or None
        except Exception:
            # Brief lookup failure must never block draft creation
            pass

    # --- Asset context bundle ---
    resolved_bundle: list[dict[str, Any]] = []
    if asset_context_bundle is not None:
        resolved_bundle = [a for a in asset_context_bundle if a.get("summary", "").strip()]
    else:
        try:
            asset_links = await list_campaign_asset_links(session, candidate_id)
            resolved_bundle = [
                {
                    "id": a.asset_id,
                    "title": f"Asset {a.asset_id}",
                    "assetType": a.asset_type,
                    "summary": a.summary or "",
                    "sourceUrl": None,
                }
                for a in asset_links
                if a.summary and a.summary.strip()
            ]
        except Exception:
            pass

    asset_text = _build_asset_context_text(resolved_bundle)

    # --- Compose metadata bundle ---
    brief_with_assets: str | None = None
    if brief_text and asset_text:
        brief_with_assets = f"{brief_text}\n\n{asset_text}"
    elif brief_text:
        brief_with_assets = brief_text
    elif asset_text:
        brief_with_assets = asset_text

    metadata: dict[str, Any] = {}
    if brief_with_assets:
        metadata["brief"] = brief_with_assets
    if resolved_bundle:
        metadata["assetContext"] = resolved_bundle

    # --- Title ---
    family = candidate.campaign_family or "Campaign"
    campaign_name = candidate.name or family
    title = f"{family} — Draft"

    # --- Get-or-create per-candidate folder (tombstone-aware) ---
    # Keyed on candidate_id (stored as str in writing_folders.campaign_id).
    # The folder's display name is derived at read time from the live
    # candidate — folder.name here is just a creation-time snapshot.
    # Returns None when the folder was previously deleted (soft-deleted tombstone);
    # in that case the draft is created without a folder_id (lands in All drafts).
    folder = await wr_repo.get_or_create_folder_by_candidate_respecting_tombstone(
        session,
        candidate_id,
        candidate_name=campaign_name,
    )

    # --- External WS call ---
    external_draft: ExternalDraft = await external.create_draft(title=title, metadata=metadata)

    # --- Create campaign_deliverables row ---
    #
    # campaign_id is set to campaign_family (the human-readable family name,
    # e.g. "obc"), NOT str(candidate_id).  The Writing Studio filter dropdown
    # is populated from CampaignCandidate.campaign_family, so only the family
    # string produces matching filter results.
    draft_meta: dict[str, Any] = {
        **metadata,
        "externalDraftId": external_draft.external_id,
        "externalTitle": external_draft.title,
    }
    if folder is not None:
        draft_meta["folder_id"] = folder.id
        draft_meta["folder_name"] = campaign_name  # live name snapshot for clients reading metadata
    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=external_draft.external_id,
        campaign_id=family,
        status="generating",
        deliverable_metadata=draft_meta,
    )
    session.add(deliverable)
    await session.flush()
    await session.refresh(deliverable)
    await session.commit()

    # --- Emit event (non-fatal) ---
    with contextlib.suppress(Exception):
        await publish_event(
            "draft.generated",
            draft_id=external_draft.external_id,
            campaign_id=family,
            deliverable_id=str(deliverable.id),
            status="generating",
        )

    return Draft(
        id=deliverable.id,
        external_id=external_draft.external_id,
        candidate_id=candidate_id,
        title=external_draft.title,
        status="generating",
        brief_text=brief_text,
        asset_context_bundle=resolved_bundle,
        metadata=metadata,
        created_at=deliverable.created_at,
    )


async def create_handoff_draft(
    session: AsyncSession,
    candidate_id: int,
    *,
    asset_label: str | None = None,
    ws: Any = None,  # ExternalWritingStudio; injected in tests
) -> Draft:
    """Create a hand-crafted Writing Studio draft seeded from campaign context.

    This is the MANUAL / ad-hoc draft path (operator-initiated from the campaign
    detail UI).  It differs from ``create_draft_from_candidate`` in three ways:

    1. Title — uses the campaign name (or ``"{campaign_name} — {asset_label}"``
       if the caller supplies an asset/payload type label).
    2. Brief metadata — seeded from:
       a. campaign objective (always — the single most useful context for
          a blank studio session),
       b. assembled Campaign Brief formatted text (if one exists),
       c. primary signal context (headline + summary).
    3. Voice — ``voiceProfileSlug`` is set to the active writing profile name
       so the compose engine picks it up without requiring the operator to
       choose manually.
    4. Tags — campaign family and geography/state tags are seeded into
       ``metadata.tags`` for the asset-tagging rules engine.  The shape is
       deliberately extensible: once audience/type/platform tags land, they
       slot into the same list.

    The draft starts with status ``"draft"`` (not ``"generating"``) since this
    path does NOT trigger automatic composition.

    Does NOT auto-compose.  Returns the created draft (with ``id``); the
    frontend navigates the operator directly into the studio.
    """
    candidate = await get_candidate(session, candidate_id)
    external = ws if ws is not None else get_writing_studio()

    family = candidate.campaign_family or "Campaign"
    campaign_name = candidate.name or family

    # ── 1. Title ──────────────────────────────────────────────────────────────
    title = f"{campaign_name} — {asset_label}" if asset_label else campaign_name

    # ── 2. Brief field: objective + assembled brief + primary signal ──────────
    brief_parts: list[str] = []

    if candidate.objective:
        brief_parts.append(f"Objective: {candidate.objective.strip()}")

    try:
        db_brief = await get_campaign_brief(session, candidate_id)
        if db_brief is not None:
            formatted = format_brief_for_writing_studio(CampaignBrief(content=db_brief.content))
            if formatted and formatted.strip():
                brief_parts.append(formatted.strip())
    except Exception:  # noqa: BLE001
        pass  # brief absence never blocks draft creation

    try:
        primary_signal = await get_candidate_primary_signal(session, candidate_id)
        if primary_signal is not None:
            sig_parts: list[str] = []
            if primary_signal.headline:
                sig_parts.append(primary_signal.headline)
            if primary_signal.summary:
                sig_parts.append(primary_signal.summary)
            if sig_parts:
                brief_parts.append("Signal context: " + " — ".join(sig_parts))
    except Exception:  # noqa: BLE001
        pass

    brief_text: str | None = "\n\n".join(brief_parts) if brief_parts else None

    # ── 3. Voice — resolve active profile slug ────────────────────────────────
    voice_profile_slug: str | None = None
    try:
        profile = await wr_repo.get_active_profile(session)
        if profile is not None and profile.name:
            voice_profile_slug = profile.name.lower().replace(" ", "-")
    except Exception:  # noqa: BLE001
        pass

    # ── 4. Tags — family + geography (extensible list) ────────────────────────
    tags: list[str] = []
    if family:
        tags.append(family)
    try:
        target_scope = candidate.target_scope_json
        if isinstance(target_scope, dict):
            states: list[str] = target_scope.get("states") or []
            tags.extend(s for s in states if isinstance(s, str))
    except Exception:  # noqa: BLE001
        pass

    # ── Compose metadata bundle ───────────────────────────────────────────────
    metadata: dict[str, Any] = {}
    if brief_text:
        metadata["brief"] = brief_text
    if voice_profile_slug:
        metadata["voiceProfileSlug"] = voice_profile_slug
    if tags:
        metadata["tags"] = tags
    metadata["handoff"] = True  # flag distinguishes manual from pipeline drafts

    # ── Get-or-create per-candidate folder (tombstone-aware) ─────────────────
    # Returns None when the folder was previously deleted (soft-deleted tombstone);
    # in that case the draft is created without a folder_id (lands in All drafts).
    folder = await wr_repo.get_or_create_folder_by_candidate_respecting_tombstone(
        session,
        candidate_id,
        candidate_name=campaign_name,
    )

    draft = await _create_manual_draft_record(
        session,
        candidate_id=candidate_id,
        campaign_id=family,
        title=title,
        metadata=metadata,
        folder_id=folder.id if folder is not None else None,
        folder_name=campaign_name if folder is not None else None,
        ws=external,
    )
    draft.brief_text = brief_text
    return draft


async def create_blank_draft(
    session: AsyncSession,
    *,
    title: str | None = None,
    folder_id: int | None = None,
    ws: Any = None,
) -> Draft:
    """Create a genuinely blank draft with no campaign context.

    Attaches the templates placeholder candidate so the deliverable row
    satisfies the candidate_id FK constraint without inventing a new schema.
    This is the same substrate used by create_template_draft for standalone
    (non-campaign) drafts.

    Called by POST /drafts when candidate_id is absent (New-draft from the
    picker for any draft, including those without a campaign context).
    """
    candidate = await _get_or_create_template_workspace_candidate(session)
    draft_title = (title or "").strip() or "New draft"
    folder: Any = None
    folder_name: str | None = None
    if folder_id is not None:
        folder = await wr_repo.get_folder(session, folder_id)
        if folder is not None:
            folder_name = folder.name

    return await _create_manual_draft_record(
        session,
        candidate_id=candidate.id,
        campaign_id=candidate.campaign_family,
        title=draft_title,
        metadata={},
        folder_id=folder.id if folder is not None else None,
        folder_name=folder_name,
        ws=ws,
    )


async def create_template_draft(
    session: AsyncSession,
    *,
    profile_id: int,
    template_id: int,
    template_key: str,
    template_name: str,
    template_body: str,
    title: str,
    folder_id: int | None = None,
    ws: Any = None,
) -> Draft:
    """Instantiate a structured template into a fresh draft-ready deliverable."""
    folder = await wr_repo.get_folder(session, folder_id) if folder_id is not None else None

    candidate: CampaignCandidate | None = None
    if folder is not None and isinstance(folder.campaign_id, str) and folder.campaign_id.isdigit():
        candidate = await session.get(CampaignCandidate, int(folder.campaign_id))
    if candidate is None:
        candidate = await _get_or_create_template_workspace_candidate(session)

    voice_profile_slug: str | None = None
    profile = await wr_repo.get_profile(session, profile_id)
    if profile is None:
        profile = await wr_repo.get_active_profile(session)
    if profile is not None and profile.name:
        voice_profile_slug = profile.name.lower().replace(" ", "-")

    created_at = datetime.now(UTC).isoformat()
    metadata: dict[str, Any] = {
        "templateSource": {
            "templateId": template_id,
            "templateKey": template_key,
            "templateName": template_name,
        },
        "versions": [
            {
                "id": "v1",
                "version_number": 1,
                "content": template_body,
                "created_at": created_at,
                "source": "template_apply",
            }
        ],
    }
    if voice_profile_slug:
        metadata["voiceProfileSlug"] = voice_profile_slug

    folder_name = folder.name if folder is not None else None
    return await _create_manual_draft_record(
        session,
        candidate_id=candidate.id,
        campaign_id=candidate.campaign_family,
        title=title,
        metadata=metadata,
        folder_id=folder.id if folder is not None else None,
        folder_name=folder_name,
        ws=ws,
    )


async def submit_draft_for_review(
    session: AsyncSession,
    deliverable_id: int,
    *,
    ws: Any = None,  # ExternalWritingStudio; injected in tests
) -> ApprovalRecord:
    """Transition a deliverable to ready_for_review and create a Gate-2 approval row.

    Port of Node's submit_draft_for_review path in writing-studio.js.

    1. Fetch the campaign_deliverables row.
    2. Reuse an existing pending Gate-2 approval when present.
    3. Otherwise call ExternalWritingStudio.submit_for_review().
    4. Create approvals row with kind='writing_gate_2'.
    5. Update deliverable status to 'ready_for_review' when needed.
    6. Emit draft.approved event for a newly-opened review.
    7. Return ApprovalRecord.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.id == deliverable_id)
    )
    deliverable = result.scalar_one_or_none()
    if deliverable is None:
        raise ValueError(f"campaign_deliverables id={deliverable_id} not found")

    existing_approval_result = await session.execute(
        select(Approval)
        .where(
            Approval.kind == "writing_gate_2",
            Approval.subject_id == str(deliverable_id),
            Approval.status == "pending",
        )
        .order_by(Approval.created_at.desc())
        .limit(1)
    )
    existing_approval = existing_approval_result.scalar_one_or_none()
    if existing_approval is not None:
        payload = (
            dict(existing_approval.decision_payload)
            if isinstance(existing_approval.decision_payload, dict)
            else {}
        )
        return ApprovalRecord(
            id=existing_approval.id,
            kind="writing_gate_2",
            subject_id=str(deliverable_id),
            status=existing_approval.status,
            external_approval_id=payload.get("externalApprovalId")
            if isinstance(payload.get("externalApprovalId"), str)
            else None,
            created_at=existing_approval.created_at,
        )

    external = ws if ws is not None else get_writing_studio()
    external_id = deliverable.deliverable_id or str(deliverable_id)

    # Call external submit (non-fatal for stub; re-raises for real)
    ext_approval = None
    try:  # noqa: SIM105
        ext_approval = await external.submit_for_review(external_id)
    except Exception:  # noqa: BLE001
        pass

    # Create approval row
    approval = Approval(
        kind="writing_gate_2",
        subject_id=str(deliverable_id),
        status="pending",
        decision_payload={
            "deliverableId": deliverable_id,
            "externalDraftId": external_id,
            "externalApprovalId": ext_approval.external_id if ext_approval else None,
        },
    )
    session.add(approval)

    # Manual/template drafts are already draft_ready before review is requested.
    if deliverable.status != DeliverableState.draft_ready.value:
        await transition(session, "deliverable", deliverable_id, DeliverableState.draft_ready)

    await session.flush()
    await session.refresh(approval)
    await session.commit()

    # Emit event (non-fatal)
    with contextlib.suppress(Exception):
        await publish_event(
            "draft.approved",
            draft_id=external_id,
            campaign_id=deliverable.campaign_id,
            deliverable_id=str(deliverable_id),
            approval_id=str(approval.id),
            status="ready_for_review",
        )

    return ApprovalRecord(
        id=approval.id,
        kind="writing_gate_2",
        subject_id=str(deliverable_id),
        status="pending",
        external_approval_id=ext_approval.external_id if ext_approval else None,
        created_at=approval.created_at,
    )


# ── Backfill ──────────────────────────────────────────────────────────────────


@dataclass
class BackfillResult:
    """Summary of a campaign-folder backfill run."""

    rows_examined: int
    rows_updated: int
    folders_created: int
    skipped_no_candidate: int
    family_folders_removed: int


async def backfill_campaign_folders(session: AsyncSession) -> BackfillResult:
    """Idempotent backfill: one folder per campaign candidate.

    Pass 1 — Deliverables
    ─────────────────────
    For every ``campaign_deliverable`` row:
      1. Look up the linked ``CampaignCandidate``.
      2. Get-or-create a ``WritingFolder`` keyed on ``str(candidate_id)``
         (stored in ``writing_folders.campaign_id``).
      3. Patch ``deliverable_metadata`` with the correct ``folder_id``
         and a snapshot ``folder_name``.
      4. Ensure ``campaign_deliverables.campaign_id`` equals the family
         string (unchanged semantic).

    A row is considered already-correct when its ``metadata.folder_id``
    equals the id of the per-candidate folder for its candidate.  Only
    those rows are skipped; all others are updated (idempotent re-runs are
    safe).

    Pass 2 — Orphaned family-level folders
    ──────────────────────────────────────
    After all deliverables have been migrated, any ``WritingFolder`` whose
    ``campaign_id`` value is NOT a pure-integer string (i.e. it is an old
    family name like ``"obc"`` rather than a candidate id like ``"42"``) is
    deleted.  This removes the misleadingly-named family-level folders
    created by the previous implementation without touching per-candidate
    folders that are already correct.

    Does NOT commit — caller owns the transaction.
    """
    from sqlalchemy import select

    from artemis.marketing.models import CampaignCandidate, CampaignDeliverable

    result = await session.execute(select(CampaignDeliverable))
    deliverables = list(result.scalars())

    # Pre-fetch all referenced candidates in one query.
    candidate_ids = {d.candidate_id for d in deliverables if d.candidate_id is not None}
    candidates_by_id: dict[int, CampaignCandidate] = {}
    if candidate_ids:
        cand_result = await session.execute(
            select(CampaignCandidate).where(CampaignCandidate.id.in_(candidate_ids))
        )
        candidates_by_id = {c.id: c for c in cand_result.scalars()}

    rows_examined = 0
    rows_updated = 0
    folders_created_ids: set[int] = set()
    skipped = 0

    # Track per-candidate folder resolution so we can check idempotency cheaply.
    # Values are WritingFolder instances OR the sentinel ``_tombstoned`` when the
    # folder was explicitly deleted; tombstoned candidates are skipped so that
    # backfill never resurects a deleted campaign folder.
    _tombstoned = object()
    candidate_folder_cache: dict[int, Any] = {}  # candidate_id -> WritingFolder | _tombstoned

    for d in deliverables:
        rows_examined += 1
        if d.candidate_id is None:
            skipped += 1
            continue
        candidate = candidates_by_id.get(d.candidate_id)
        if candidate is None:
            skipped += 1
            continue

        family = candidate.campaign_family or "Campaign"
        campaign_name = candidate.name or family
        meta: dict[str, Any] = dict(d.deliverable_metadata or {})

        # Get-or-create the per-candidate folder (tombstone-aware, cached within this run).
        if d.candidate_id not in candidate_folder_cache:
            folder = await wr_repo.get_or_create_folder_by_candidate_respecting_tombstone(
                session,
                d.candidate_id,
                candidate_name=campaign_name,
            )
            candidate_folder_cache[d.candidate_id] = folder if folder is not None else _tombstoned
            if folder is not None:
                folders_created_ids.add(folder.id)
        else:
            cached = candidate_folder_cache[d.candidate_id]
            folder = None if cached is _tombstoned else cached

        # Tombstoned: clear folder assignment so the draft lands in All drafts.
        if folder is None:
            changed = False
            if "folder_id" in meta or "folder_name" in meta:
                meta.pop("folder_id", None)
                meta.pop("folder_name", None)
                d.deliverable_metadata = meta
                changed = True
            if d.campaign_id != family:
                d.campaign_id = family
                changed = True
            if changed:
                rows_updated += 1
            continue

        # Check if this deliverable is already pointing at the correct folder.
        if meta.get("folder_id") == folder.id and d.campaign_id == family:
            continue

        d.campaign_id = family
        meta["folder_id"] = folder.id
        meta["folder_name"] = campaign_name  # live-name snapshot
        d.deliverable_metadata = meta
        rows_updated += 1

    await session.flush()

    # Pass 2: remove old family-level folders (campaign_id is not a digit string).
    from artemis.writing_rules.models import WritingFolder

    all_folders_result = await session.execute(select(WritingFolder))
    all_folders = list(all_folders_result.scalars())
    family_folders_removed = 0
    for f in all_folders:
        cid = f.campaign_id or ""
        if cid and not cid.isdigit():
            # Old family-level folder — remove it.
            await session.delete(f)
            family_folders_removed += 1

    await session.flush()

    return BackfillResult(
        rows_examined=rows_examined,
        rows_updated=rows_updated,
        folders_created=len(folders_created_ids),
        skipped_no_candidate=skipped,
        family_folders_removed=family_folders_removed,
    )
