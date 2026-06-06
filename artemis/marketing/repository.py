"""Repository helpers for the Marketing OS domain.

All functions are async and accept a SQLAlchemy AsyncSession.
No business logic here — just DB read/write helpers.

Convention: functions raise ValueError for not-found or conflict
conditions that callers should handle. Callers own the commit/rollback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Select, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.district_classifier import classify_tier, is_supported
from artemis.marketing.initiation_schemas import CampaignInitiationProposal, TargetScope
from artemis.marketing.models import (
    Approval,
    CampaignBrief,
    CampaignCandidate,
    CampaignCandidateSignal,
    CampaignDeliverable,
    ContentAsset,
    ContentAssetLink,
    DeliverableType,
    District,
    DistrictTierBand,
    MarketingClusteringConfig,
    Ruleset,
    ScoutRun,
    SignalQueue,
    TerritoryConfig,
)
from artemis.marketing.state_machine import WorkspaceState


@dataclass(slots=True)
class CandidatePredecessorContext:
    candidate_id: int
    name: str | None
    objective: str | None
    latest_brief: dict[str, Any] | None
    linked_assets: list[dict[str, Any]]


@dataclass(slots=True)
class CandidateLineageContext:
    candidate_id: int
    name: str | None
    objective: str | None
    latest_brief: dict[str, Any] | None
    linked_assets: list[dict[str, Any]]
    drafts: list[dict[str, Any]]


# ─────────────────────────────────────────────────────────────────────────────
# Signal Queue
# ─────────────────────────────────────────────────────────────────────────────


async def create_signal(session: AsyncSession, **kwargs: Any) -> SignalQueue:
    """Insert a new signal_queue row and flush to get the server-assigned id."""
    signal = SignalQueue(**kwargs)
    session.add(signal)
    await session.flush()
    await session.refresh(signal)
    return signal


async def find_signal_by_dedupe_key(
    session: AsyncSession,
    source_url: str,
    headline: str,
) -> SignalQueue | None:
    """Return the first active (in_inbox or approved) signal matching url+headline.

    Mirrors the Node app's dedupeByUrlHeadline prepared statement.
    """
    result = await session.execute(
        select(SignalQueue)
        .where(
            SignalQueue.source_url == source_url,
            SignalQueue.headline == headline,
            SignalQueue.signal_status.in_(["pending_qualification", "qualified", "approved"]),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_signals(
    session: AsyncSession,
    *,
    status: str | None = None,
    campaign_family: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> list[SignalQueue]:
    """Cursor-based paginated list of signals, newest first."""
    q = select(SignalQueue)
    if status:
        q = q.where(SignalQueue.signal_status == status)
    if campaign_family:
        q = q.where(SignalQueue.campaign_family == campaign_family)
    if cursor is not None:
        q = q.where(SignalQueue.id < cursor)
    q = q.order_by(SignalQueue.id.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_signal(session: AsyncSession, signal_id: int) -> SignalQueue:
    signal = await session.get(SignalQueue, signal_id)
    if signal is None:
        raise ValueError(f"signal_queue id={signal_id} not found")
    return signal


async def update_signal(session: AsyncSession, signal_id: int, **kwargs: Any) -> SignalQueue:
    signal = await get_signal(session, signal_id)
    for key, value in kwargs.items():
        setattr(signal, key, value)
    signal.updated_at = datetime.now(tz=UTC)
    await session.flush()
    return signal


async def save_signal_qualification(
    session: AsyncSession,
    signal_id: int,
    qualification_json: dict[str, Any],
) -> SignalQueue:
    """Attach a qualification result to a signal."""
    return await update_signal(session, signal_id, qualification_json=qualification_json)


# ─────────────────────────────────────────────────────────────────────────────
# Rulesets
# ─────────────────────────────────────────────────────────────────────────────


async def get_active_ruleset_version(session: AsyncSession, family: str) -> Ruleset | None:
    """Return the single active ruleset for a campaign family, or None."""
    result = await session.execute(
        select(Ruleset)
        .where(Ruleset.family == family, Ruleset.state == "active")
        .order_by(Ruleset.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def list_ruleset_versions(session: AsyncSession, family: str | None = None) -> list[Ruleset]:
    """List all ruleset versions, optionally filtered by family."""
    q = select(Ruleset)
    if family:
        q = q.where(Ruleset.family == family)
    q = q.order_by(Ruleset.family, Ruleset.created_at.desc())
    result = await session.execute(q)
    return list(result.scalars().all())


async def activate_ruleset_version(session: AsyncSession, ruleset_id: int) -> Ruleset:
    """Activate a ruleset version; archive any previously active version for the family.

    Transaction is the caller's responsibility.
    """
    ruleset = await session.get(Ruleset, ruleset_id)
    if ruleset is None:
        raise ValueError(f"ruleset id={ruleset_id} not found")

    # Archive any currently active version for the same family
    await session.execute(
        update(Ruleset)
        .where(Ruleset.family == ruleset.family, Ruleset.state == "active")
        .values(state="archived")
    )
    ruleset.state = "active"
    await session.flush()
    return ruleset


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Candidates
# ─────────────────────────────────────────────────────────────────────────────


async def create_campaign_candidate_from_signal(
    session: AsyncSession,
    signal_id: int,
    ruleset_version_tag: str,
    qualification_summary: dict[str, Any] | None = None,
) -> CampaignCandidate:
    """Promote a qualified signal to a campaign candidate.

    Sets decision_state='in_inbox' (awaiting Gate 1 decision) and
    workspace_state='pending_content' on creation. Use transition() to
    advance decision_state to approved/rejected/etc.
    """
    signal = await get_signal(session, signal_id)
    candidate = CampaignCandidate(
        source_signal_id=signal_id,
        campaign_family=signal.campaign_family,
        stage="human_gate_1",
        decision_state="in_inbox",
        workspace_state=WorkspaceState.pending_content,
        ruleset_version_at_qualification=ruleset_version_tag,
        metrics_json=qualification_summary,
    )
    session.add(candidate)
    await session.flush()
    await session.refresh(candidate)
    return candidate


async def list_candidates(
    session: AsyncSession,
    *,
    decision_state: str | None = None,
    campaign_family: str | None = None,
    limit: int = 50,
    cursor: int | None = None,
) -> list[CampaignCandidate]:
    q = select(CampaignCandidate)
    if decision_state:
        q = q.where(CampaignCandidate.decision_state == decision_state)
    if campaign_family:
        q = q.where(CampaignCandidate.campaign_family == campaign_family)
    if cursor is not None:
        q = q.where(CampaignCandidate.id < cursor)
    q = q.order_by(CampaignCandidate.id.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())


async def get_candidate(session: AsyncSession, candidate_id: int) -> CampaignCandidate:
    candidate = await session.get(CampaignCandidate, candidate_id)
    if candidate is None:
        raise ValueError(f"campaign_candidates id={candidate_id} not found")
    return candidate


async def list_deliverable_types(
    session: AsyncSession, active_only: bool = True
) -> list[DeliverableType]:
    stmt = select(DeliverableType).order_by(DeliverableType.display_order, DeliverableType.id)
    if active_only:
        stmt = stmt.where(DeliverableType.active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_cluster_window_days(session: AsyncSession) -> int:
    result = await session.execute(select(MarketingClusteringConfig.cluster_window_days).limit(1))
    cluster_window_days = result.scalar_one_or_none()
    return cluster_window_days if cluster_window_days is not None else 90


async def _attach_signal_to_candidate(
    session: AsyncSession,
    candidate: CampaignCandidate,
    signal: SignalQueue,
    *,
    is_primary: bool,
) -> None:
    existing = await session.execute(
        select(CampaignCandidateSignal).where(
            CampaignCandidateSignal.candidate_id == candidate.id,
            CampaignCandidateSignal.signal_id == signal.id,
        )
    )
    candidate_signal = existing.scalar_one_or_none()
    if candidate_signal is not None:
        return

    session.add(
        CampaignCandidateSignal(
            candidate_id=candidate.id,
            signal_id=signal.id,
            is_primary=is_primary,
        )
    )
    await session.flush()


def _candidate_district_family_stmt(signal: SignalQueue) -> Select[tuple[CampaignCandidate]]:
    return (
        select(CampaignCandidate)
        .join(SignalQueue, CampaignCandidate.source_signal_id == SignalQueue.id)
        .where(
            SignalQueue.resolved_district_id == signal.resolved_district_id,
            CampaignCandidate.campaign_family == signal.campaign_family,
        )
    )


async def cluster_or_create_candidate(
    session: AsyncSession, signal: SignalQueue
) -> CampaignCandidate:
    """Deterministically group a signal into an open candidate or create a fresh one."""
    existing = await session.execute(
        select(CampaignCandidate)
        .join(CampaignCandidateSignal, CampaignCandidateSignal.candidate_id == CampaignCandidate.id)
        .where(CampaignCandidateSignal.signal_id == signal.id)
        .order_by(CampaignCandidate.id.desc())
        .limit(1)
    )
    attached_candidate = existing.scalar_one_or_none()
    if attached_candidate is not None:
        return attached_candidate

    if signal.resolved_district_id is not None and signal.campaign_family:
        cluster_window_days = await get_cluster_window_days(session)
        cutoff = datetime.now(tz=UTC) - timedelta(days=cluster_window_days)
        open_candidate_result = await session.execute(
            _candidate_district_family_stmt(signal)
            .where(
                CampaignCandidate.initiated_at.is_(None),
                CampaignCandidate.decision_state != "rejected",
                CampaignCandidate.created_at >= cutoff,
            )
            .order_by(CampaignCandidate.created_at.desc(), CampaignCandidate.id.desc())
            .limit(1)
        )
        open_candidate = open_candidate_result.scalar_one_or_none()
        if open_candidate is not None:
            await _attach_signal_to_candidate(session, open_candidate, signal, is_primary=False)
            return open_candidate

    predecessor_id: int | None = None
    if signal.resolved_district_id is not None and signal.campaign_family:
        predecessor_result = await session.execute(
            _candidate_district_family_stmt(signal)
            .where(
                (CampaignCandidate.initiated_at.is_not(None))
                | (CampaignCandidate.decision_state == "rejected")
            )
            .order_by(CampaignCandidate.created_at.desc(), CampaignCandidate.id.desc())
            .limit(1)
        )
        predecessor = predecessor_result.scalar_one_or_none()
        predecessor_id = predecessor.id if predecessor is not None else None

    candidate = CampaignCandidate(
        source_signal_id=signal.id,
        campaign_family=signal.campaign_family,
        stage="human_gate_1",
        decision_state="in_inbox",
        workspace_state=WorkspaceState.pending_content,
        predecessor_id=predecessor_id,
    )
    session.add(candidate)
    await session.flush()
    await _attach_signal_to_candidate(session, candidate, signal, is_primary=True)
    await session.refresh(candidate)
    return candidate


@dataclass(slots=True)
class SignalPromotionResult:
    """Outcome of promoting one qualified signal to a campaign candidate."""

    signal: SignalQueue
    candidate: CampaignCandidate
    created: bool  # True if a new candidate was created; False if clustered onto existing


async def promote_signal_to_candidate(
    session: AsyncSession,
    signal: SignalQueue,
) -> SignalPromotionResult:
    """Shared promotion: cluster/create candidate for *one* qualified signal + mark it approved.

    This is the single source of truth for Gate-1 signal promotion.  Both the
    manual per-signal path (POST /api/signal-queue/{id}/approve) and the pipeline
    gate path (Gate-1 signal_brief approval) call this function so they cannot drift.

    Callers are responsible for flushing/committing the session.  The signal must
    be in ``qualified`` status; if it is already ``approved`` the call is a no-op
    (returns the existing candidate without re-transitioning).
    """
    from artemis.marketing.state_machine import SignalState, transition

    # Idempotency: if already approved, just return the existing candidate.
    if signal.signal_status == SignalState.APPROVED:
        candidate = await cluster_or_create_candidate(session, signal)
        return SignalPromotionResult(signal=signal, candidate=candidate, created=False)

    existing_link = await session.execute(
        select(CampaignCandidateSignal).where(
            CampaignCandidateSignal.signal_id == signal.id,
        )
    )
    had_candidate = existing_link.scalar_one_or_none() is not None

    candidate = await cluster_or_create_candidate(session, signal)
    created = not had_candidate

    await transition(session, "signal", signal.id, SignalState.APPROVED)
    await session.flush()

    return SignalPromotionResult(signal=signal, candidate=candidate, created=created)


async def promote_qualified_signals_for_run(
    session: AsyncSession,
    pipeline_run_id: str,
) -> list[SignalPromotionResult]:
    """Promote all qualified signals for a pipeline run to campaign candidate(s).

    Called by the pipeline Gate-1 approval path so that ``content_brief_assembler``
    (which calls ``list_run_candidates``) finds exactly one uninitiated candidate.

    The clustering logic in ``cluster_or_create_candidate`` deterministically
    groups signals by (resolved_district_id, campaign_family); in the common case
    all signals from one scout run share the same district+family and end up in a
    single candidate.  If they span multiple district+family combinations the
    assembler will see N candidates; the brief states that is expected to produce
    exactly-one for the current marketing pipeline — the scout run is scoped to
    one district/family.

    Signals already in ``approved`` status are skipped (idempotent).
    """
    rows = (
        (
            await session.execute(
                select(SignalQueue).where(
                    SignalQueue.pipeline_run_id == pipeline_run_id,
                    SignalQueue.signal_status == "qualified",
                )
            )
        )
        .scalars()
        .all()
    )

    results: list[SignalPromotionResult] = []
    for signal in rows:
        result = await promote_signal_to_candidate(session, signal)
        results.append(result)
    return results


async def promote_selected_signals_for_run(
    session: AsyncSession,
    pipeline_run_id: str,
    selected_signal_ids: list[int],
) -> list[SignalPromotionResult]:
    """Promote only the operator-selected qualified signals for a pipeline run.

    Loads the qualified signals from this run whose IDs are in
    ``selected_signal_ids``, calls ``promote_signal_to_candidate`` on each
    (so related selected signals auto-cluster via ``cluster_or_create_candidate``),
    and returns the promotion results.

    Unselected qualified signals are left in ``qualified`` status — they are NOT
    deleted or rejected (lossless rule).

    Callers are responsible for committing the session.
    """
    if not selected_signal_ids:
        return []

    rows = (
        (
            await session.execute(
                select(SignalQueue).where(
                    SignalQueue.pipeline_run_id == pipeline_run_id,
                    SignalQueue.signal_status == "qualified",
                    SignalQueue.id.in_(selected_signal_ids),
                )
            )
        )
        .scalars()
        .all()
    )

    results: list[SignalPromotionResult] = []
    for signal in rows:
        result = await promote_signal_to_candidate(session, signal)
        results.append(result)
    return results


async def get_signal_ids_for_cluster_keys(
    session: AsyncSession,
    pipeline_run_id: str,
    cluster_keys: list[str],
) -> list[int]:
    """Expand a list of cluster_keys into qualified signal IDs for a pipeline run.

    A cluster_key has the form ``"{resolved_district_id}|{campaign_family}"``.
    Returns the IDs of all qualified signals in this run that match any of those
    clusters.  Used by the approvals route to convert cluster_keys → signal_ids
    before calling ``promote_selected_signals_for_run``.
    """
    if not cluster_keys:
        return []

    rows = (
        (
            await session.execute(
                select(SignalQueue).where(
                    SignalQueue.pipeline_run_id == pipeline_run_id,
                    SignalQueue.signal_status == "qualified",
                )
            )
        )
        .scalars()
        .all()
    )

    signal_ids: list[int] = []
    for row in rows:
        dist_id = row.resolved_district_id
        family = row.campaign_family or ""
        key = f"{dist_id}|{family}"
        if key in cluster_keys:
            signal_ids.append(row.id)
    return signal_ids


async def get_candidate_signals(
    session: AsyncSession, candidate_id: int
) -> list[CampaignCandidateSignal]:
    await get_candidate(session, candidate_id)
    result = await session.execute(
        select(CampaignCandidateSignal)
        .where(CampaignCandidateSignal.candidate_id == candidate_id)
        .order_by(
            CampaignCandidateSignal.is_primary.desc(),
            CampaignCandidateSignal.attached_at.asc(),
            CampaignCandidateSignal.id.asc(),
        )
    )
    return list(result.scalars().all())


async def get_candidate_signal_rows(session: AsyncSession, candidate_id: int) -> list[SignalQueue]:
    await get_candidate(session, candidate_id)
    result = await session.execute(
        select(SignalQueue)
        .join(CampaignCandidateSignal, CampaignCandidateSignal.signal_id == SignalQueue.id)
        .where(CampaignCandidateSignal.candidate_id == candidate_id)
        .order_by(
            CampaignCandidateSignal.is_primary.desc(),
            CampaignCandidateSignal.attached_at.asc(),
            CampaignCandidateSignal.id.asc(),
        )
    )
    return list(result.scalars().all())


async def get_candidate_primary_signal(
    session: AsyncSession, candidate_id: int
) -> SignalQueue | None:
    result = await session.execute(
        select(SignalQueue)
        .join(CampaignCandidateSignal, CampaignCandidateSignal.signal_id == SignalQueue.id)
        .where(
            CampaignCandidateSignal.candidate_id == candidate_id,
            CampaignCandidateSignal.is_primary.is_(True),
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def save_initiation_proposal(
    session: AsyncSession,
    candidate_id: int,
    proposal: CampaignInitiationProposal | dict[str, Any] | None,
) -> CampaignCandidate:
    candidate = await get_candidate(session, candidate_id)
    candidate.initiation_proposal_json = (
        proposal.model_dump(mode="json")
        if isinstance(proposal, CampaignInitiationProposal)
        else proposal
    )
    await session.flush()
    return candidate


async def initiate_campaign(
    session: AsyncSession,
    candidate_id: int,
    *,
    name: str,
    objective: str,
    owner_user_id: int | None,
    target_scope: TargetScope | dict[str, Any],
    deliverable_type_slugs: list[str],
    initiated_by: int | None,
) -> CampaignCandidate:
    candidate = await get_candidate(session, candidate_id)
    if candidate.decision_state == "rejected":
        raise ValueError(
            f"campaign_candidates id={candidate_id} is rejected and cannot be initiated"
        )
    if candidate.initiated_at is not None:
        raise ValueError(f"campaign_candidates id={candidate_id} is already initiated")

    try:
        parsed_target_scope = (
            target_scope
            if isinstance(target_scope, TargetScope)
            else TargetScope.model_validate(target_scope)
        )
    except ValidationError as exc:
        message = exc.errors()[0].get("msg", "Invalid target_scope")
        raise ValueError(message) from exc

    active_deliverable_types = await list_deliverable_types(session, active_only=True)
    active_slugs = {row.slug for row in active_deliverable_types}
    invalid_slugs = [slug for slug in deliverable_type_slugs if slug not in active_slugs]
    if invalid_slugs:
        raise ValueError(
            "deliverableTypeSlugs must be active deliverable types. "
            f"Invalid: {', '.join(invalid_slugs)}. "
            f"Active: {', '.join(sorted(active_slugs))}"
        )

    candidate.name = name
    candidate.objective = objective
    candidate.owner_user_id = owner_user_id
    # Exclude None fields so legacy shapes serialize identically to their original
    # form (backward-compatible storage: {mode, states, tiers, district_ids} only,
    # no null base/include_district_ids keys written for legacy rows).
    candidate.target_scope_json = parsed_target_scope.model_dump(mode="json", exclude_none=True)
    candidate.deliverable_types_json = list(deliverable_type_slugs)
    candidate.initiated_at = datetime.now(tz=UTC)
    candidate.initiated_by = initiated_by
    await session.flush()
    return candidate


# ─────────────────────────────────────────────────────────────────────────────
# Campaign Briefs
# ─────────────────────────────────────────────────────────────────────────────


async def create_campaign_brief(
    session: AsyncSession,
    candidate_id: int,
    content: dict[str, Any],
    generated_by: str | None = None,
) -> CampaignBrief:
    """Create a new brief version for a candidate (append-only)."""
    # Validate candidate exists
    await get_candidate(session, candidate_id)
    brief = CampaignBrief(
        candidate_id=candidate_id,
        content=content,
        generated_by=generated_by,
    )
    session.add(brief)
    await session.flush()
    await session.refresh(brief)
    return brief


async def get_campaign_brief(session: AsyncSession, candidate_id: int) -> CampaignBrief | None:
    """Return the most recent brief for a candidate, or None."""
    result = await session.execute(
        select(CampaignBrief)
        .where(CampaignBrief.candidate_id == candidate_id)
        .order_by(CampaignBrief.generated_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_candidate_predecessor_context(
    session: AsyncSession,
    candidate_id: int,
) -> CandidatePredecessorContext | None:
    candidate = await get_candidate(session, candidate_id)
    if candidate.predecessor_id is None:
        return None

    predecessor = await get_candidate(session, candidate.predecessor_id)
    latest_brief = await get_campaign_brief(session, predecessor.id)
    asset_rows = (
        await session.execute(
            select(ContentAsset, ContentAssetLink.link_role)
            .join(ContentAssetLink, ContentAssetLink.asset_id == ContentAsset.id)
            .where(ContentAssetLink.candidate_id == predecessor.id)
            .order_by(ContentAsset.id.asc())
        )
    ).all()

    return CandidatePredecessorContext(
        candidate_id=predecessor.id,
        name=predecessor.name,
        objective=predecessor.objective,
        latest_brief=latest_brief.content if latest_brief is not None else None,
        linked_assets=[
            {
                "asset_id": asset.id,
                "asset_type": asset.asset_type,
                "summary": asset.summary,
                "metadata": asset.asset_metadata,
                "link_role": link_role,
            }
            for asset, link_role in asset_rows
        ],
    )


async def get_candidate_lineage_context(
    session: AsyncSession,
    candidate_id: int,
    *,
    max_depth: int = 10,
) -> list[CandidateLineageContext]:
    """Return the predecessor chain, nearest first, with collateral payloads."""
    lineage: list[CandidateLineageContext] = []
    seen: set[int] = set()
    current = await get_candidate(session, candidate_id)
    predecessor_id = current.predecessor_id

    while predecessor_id is not None and len(lineage) < max_depth and predecessor_id not in seen:
        seen.add(predecessor_id)
        predecessor = await get_candidate(session, predecessor_id)
        latest_brief = await get_campaign_brief(session, predecessor.id)
        asset_rows = (
            await session.execute(
                select(ContentAsset, ContentAssetLink.link_role)
                .join(ContentAssetLink, ContentAssetLink.asset_id == ContentAsset.id)
                .where(ContentAssetLink.candidate_id == predecessor.id)
                .order_by(ContentAsset.id.asc())
            )
        ).all()
        draft_rows = (
            (
                await session.execute(
                    select(CampaignDeliverable)
                    .where(CampaignDeliverable.candidate_id == predecessor.id)
                    .order_by(CampaignDeliverable.created_at.desc(), CampaignDeliverable.id.desc())
                )
            )
            .scalars()
            .all()
        )

        lineage.append(
            CandidateLineageContext(
                candidate_id=predecessor.id,
                name=predecessor.name,
                objective=predecessor.objective,
                latest_brief=latest_brief.content if latest_brief is not None else None,
                linked_assets=[
                    {
                        "asset_id": asset.id,
                        "asset_type": asset.asset_type,
                        "summary": asset.summary,
                        "metadata": asset.asset_metadata,
                        "link_role": link_role,
                    }
                    for asset, link_role in asset_rows
                ],
                drafts=[
                    {
                        "draft_id": draft.id,
                        "deliverable_id": draft.deliverable_id,
                        "campaign_id": draft.campaign_id,
                        "status": draft.status,
                        "metadata": draft.deliverable_metadata,
                    }
                    for draft in draft_rows
                ],
            )
        )

        predecessor_id = predecessor.predecessor_id

    return lineage


async def list_run_candidates(
    session: AsyncSession,
    pipeline_run_id: str,
    *,
    initiated_only: bool | None = None,
) -> list[CampaignCandidate]:
    stmt = (
        select(CampaignCandidate)
        .join(CampaignCandidateSignal, CampaignCandidateSignal.candidate_id == CampaignCandidate.id)
        .join(SignalQueue, SignalQueue.id == CampaignCandidateSignal.signal_id)
        .where(SignalQueue.pipeline_run_id == pipeline_run_id)
        .distinct()
        .order_by(CampaignCandidate.id.asc())
    )
    if initiated_only is True:
        stmt = stmt.where(CampaignCandidate.initiated_at.is_not(None))
    elif initiated_only is False:
        stmt = stmt.where(CampaignCandidate.initiated_at.is_(None))
    result = await session.execute(stmt)
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Content Assets
# ─────────────────────────────────────────────────────────────────────────────


async def create_content_asset(session: AsyncSession, **kwargs: Any) -> ContentAsset:
    # The DB column is named 'metadata' but the ORM attribute is 'asset_metadata'
    # Accept either keyword for caller convenience.
    if "metadata" in kwargs:
        kwargs["asset_metadata"] = kwargs.pop("metadata")
    asset = ContentAsset(**kwargs)
    session.add(asset)
    await session.flush()
    await session.refresh(asset)
    return asset


async def link_content_asset_to_candidate(
    session: AsyncSession,
    candidate_id: int,
    asset_id: int,
    link_role: str | None = None,
) -> ContentAssetLink:
    """Link a content asset to a campaign candidate.

    Raises ValueError on duplicate (unique constraint violation).
    """
    link = ContentAssetLink(
        candidate_id=candidate_id,
        asset_id=asset_id,
        link_role=link_role,
    )
    session.add(link)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ValueError(
            f"content_asset_links already exists for candidate={candidate_id} asset={asset_id}"
        ) from exc
    await session.refresh(link)
    return link


async def list_campaign_asset_links(
    session: AsyncSession, candidate_id: int
) -> list[ContentAssetLink]:
    result = await session.execute(
        select(ContentAssetLink).where(ContentAssetLink.candidate_id == candidate_id)
    )
    return list(result.scalars().all())


async def list_approved_content_assets(
    session: AsyncSession, *, campaign_family: str | None = None, limit: int = 50
) -> list[ContentAsset]:
    """Return approved reusable assets, optionally filtered by metadata campaign family."""
    bounded_limit = max(1, min(limit, 200))
    stmt = select(ContentAsset).where(ContentAsset.status == "approved")
    if campaign_family:
        stmt = stmt.where(
            ContentAsset.asset_metadata["campaign_family"].as_string() == campaign_family
        )
    result = await session.execute(stmt.order_by(ContentAsset.id.desc()).limit(bounded_limit))
    return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# Approvals
# ─────────────────────────────────────────────────────────────────────────────


async def create_approval(
    session: AsyncSession,
    kind: str,
    subject_id: str,
    decision_payload: dict[str, Any] | None = None,
) -> Approval:
    approval = Approval(
        kind=kind,
        subject_id=subject_id,
        status="pending",
        decision_payload=decision_payload,
    )
    session.add(approval)
    await session.flush()
    await session.refresh(approval)
    return approval


async def decide_approval(
    session: AsyncSession,
    approval_id: int,
    decision: str,
    decided_by: str,
    decision_payload: dict[str, Any] | None = None,
) -> Approval:
    approval = await session.get(Approval, approval_id)
    if approval is None:
        raise ValueError(f"approvals id={approval_id} not found")
    approval.status = decision
    approval.decided_by = decided_by
    approval.decided_at = datetime.now(tz=UTC)
    if decision_payload is not None:
        approval.decision_payload = decision_payload
    await session.flush()
    return approval


# ─────────────────────────────────────────────────────────────────────────────
# Districts
# ─────────────────────────────────────────────────────────────────────────────


async def get_district(session: AsyncSession, district_id: int) -> District | None:
    return await session.get(District, district_id)


async def get_tier_bands(session: AsyncSession) -> list[DistrictTierBand]:
    result = await session.execute(
        select(DistrictTierBand).order_by(DistrictTierBand.display_order)
    )
    return list(result.scalars().all())


async def upsert_district(
    session: AsyncSession,
    *,
    nces_id: str | None,
    name: str,
    state: str | None,
    enrollment: int | None,
    on_skip_list: bool = False,
    source: str,
) -> District:
    normalized_nces_id = nces_id.strip() if nces_id else None
    normalized_name = name.strip()
    normalized_state = state.strip().upper() if state else None
    bands = await get_tier_bands(session)
    tier = classify_tier(enrollment, bands)
    classified_at = datetime.now(tz=UTC)

    district: District | None = None
    if normalized_nces_id is not None:
        # When an nces_id is supplied, it is the SOLE identity key. Do NOT
        # fall back to name+state — distinct districts can share a name
        # (Ohio has multiple "Buckeye Local", each with its own nces_id), so
        # a fallback collapses them onto one row and the next CSV pass keeps
        # overwriting nces_id back and forth.
        district = await session.scalar(
            select(District)
            .where(District.nces_id == normalized_nces_id)
            .order_by(District.id.desc())
            .limit(1)
        )
    else:
        stmt = select(District).where(
            District.name == normalized_name,
            District.nces_id.is_(None),
        )
        if normalized_state is None:
            stmt = stmt.where(District.state.is_(None))
        else:
            stmt = stmt.where(District.state == normalized_state)
        district = await session.scalar(stmt.order_by(District.id.desc()).limit(1))

    if district is None:
        district = District(
            nces_id=normalized_nces_id,
            name=normalized_name,
            state=normalized_state,
            enrollment=enrollment,
            tier=tier,
            supported=is_supported(tier),
            on_skip_list=on_skip_list,
            classification_source=source,
            classified_at=classified_at,
        )
        session.add(district)
    else:
        district.nces_id = normalized_nces_id
        district.name = normalized_name
        district.state = normalized_state
        district.enrollment = enrollment
        district.tier = tier
        district.supported = is_supported(tier)
        district.on_skip_list = on_skip_list
        district.classification_source = source
        district.classified_at = classified_at

    district.updated_at = classified_at
    await session.flush()
    await session.refresh(district)
    return district


async def recompute_all_tiers(session: AsyncSession) -> int:
    bands = await get_tier_bands(session)
    result = await session.execute(select(District).order_by(District.id))
    districts = list(result.scalars().all())
    now = datetime.now(tz=UTC)

    for district in districts:
        district.tier = classify_tier(district.enrollment, bands)
        district.supported = is_supported(district.tier)
        district.classified_at = now
        district.updated_at = now

    await session.flush()
    return len(districts)


# ─────────────────────────────────────────────────────────────────────────────
# Territory Config
# ─────────────────────────────────────────────────────────────────────────────


async def get_territory_config(session: AsyncSession, family: str) -> TerritoryConfig | None:
    result = await session.execute(
        select(TerritoryConfig).where(TerritoryConfig.family == family).limit(1)
    )
    return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Scout Runs
# ─────────────────────────────────────────────────────────────────────────────


async def create_scout_run(
    session: AsyncSession,
    run_id: str,
    scout_type: str,
    status: str = "pending",
) -> ScoutRun:
    run = ScoutRun(
        id=run_id,
        scout_type=scout_type,
        status=status,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run


async def update_scout_run(session: AsyncSession, run_id: str, **kwargs: Any) -> ScoutRun:
    run = await get_scout_run(session, run_id)
    for key, value in kwargs.items():
        setattr(run, key, value)
    await session.flush()
    return run


async def get_scout_run(session: AsyncSession, run_id: str) -> ScoutRun:
    run = await session.get(ScoutRun, run_id)
    if run is None:
        raise ValueError(f"scout_runs id={run_id} not found")
    return run


async def list_scout_runs(
    session: AsyncSession,
    *,
    scout_type: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[ScoutRun]:
    q = select(ScoutRun)
    if scout_type:
        q = q.where(ScoutRun.scout_type == scout_type)
    if status:
        q = q.where(ScoutRun.status == status)
    q = q.order_by(ScoutRun.started_at.desc()).limit(limit)
    result = await session.execute(q)
    return list(result.scalars().all())
