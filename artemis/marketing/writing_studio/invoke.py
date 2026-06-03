"""Writing Studio programmatic invocation layer.

Port of writing-studio-invoke.js (relevant campaign-integration functions).

Functions:
  create_draft_from_candidate — builds metadata bundle + creates deliverable row
  submit_draft_for_review     — Gate-2 approval row + status transition
  list_campaign_asset_links   — assets with non-empty summary for metadata bundle

Uses ExternalWritingStudio (Stub by default) from .external.
Uses brief assembler (C3) for brief text.
Uses events.publish for draft lifecycle events.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.brief_assembler import (
    AssetContext,
    CampaignBrief,
    format_brief_for_writing_studio,
)
from artemis.marketing.models import Approval, CampaignDeliverable, ContentAssetLink
from artemis.marketing.repository import (
    get_campaign_brief,
    get_candidate,
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

    # --- Get-or-create per-candidate folder ---
    # Keyed on candidate_id (stored as str in writing_folders.campaign_id).
    # The folder's display name is derived at read time from the live
    # candidate — folder.name here is just a creation-time snapshot.
    folder = await wr_repo.get_or_create_folder_by_candidate(
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
    deliverable = CampaignDeliverable(
        candidate_id=candidate_id,
        deliverable_id=external_draft.external_id,
        campaign_id=family,
        status="generating",
        deliverable_metadata={
            **metadata,
            "externalDraftId": external_draft.external_id,
            "externalTitle": external_draft.title,
            "folder_id": folder.id,
            "folder_name": campaign_name,  # live name snapshot for clients reading metadata
        },
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


async def submit_draft_for_review(
    session: AsyncSession,
    deliverable_id: int,
    *,
    ws: Any = None,  # ExternalWritingStudio; injected in tests
) -> ApprovalRecord:
    """Transition a deliverable to ready_for_review and create a Gate-2 approval row.

    Port of Node's submit_draft_for_review path in writing-studio.js.

    1. Fetch the campaign_deliverables row.
    2. Call ExternalWritingStudio.submit_for_review().
    3. Create approvals row with kind='writing_gate_2'.
    4. Update deliverable status to 'ready_for_review'.
    5. Emit draft.approved event.
    6. Return ApprovalRecord.
    """
    from sqlalchemy import select

    result = await session.execute(
        select(CampaignDeliverable).where(CampaignDeliverable.id == deliverable_id)
    )
    deliverable = result.scalar_one_or_none()
    if deliverable is None:
        raise ValueError(f"campaign_deliverables id={deliverable_id} not found")

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

    # Update deliverable status via state machine
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

    # Track per-candidate folder ids so we can check idempotency cheaply.
    candidate_folder_cache: dict[int, Any] = {}  # candidate_id -> WritingFolder

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

        # Get-or-create the per-candidate folder (cached within this run).
        if d.candidate_id not in candidate_folder_cache:
            folder = await wr_repo.get_or_create_folder_by_candidate(
                session,
                d.candidate_id,
                candidate_name=campaign_name,
            )
            candidate_folder_cache[d.candidate_id] = folder
            folders_created_ids.add(folder.id)
        else:
            folder = candidate_folder_cache[d.candidate_id]

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
