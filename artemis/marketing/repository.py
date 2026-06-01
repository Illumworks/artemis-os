"""Repository helpers for the Marketing OS domain.

All functions are async and accept a SQLAlchemy AsyncSession.
No business logic here — just DB read/write helpers.

Convention: functions raise ValueError for not-found or conflict
conditions that callers should handle. Callers own the commit/rollback.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.marketing.district_classifier import classify_tier, is_supported
from artemis.marketing.models import (
    Approval,
    CampaignBrief,
    CampaignCandidate,
    ContentAsset,
    ContentAssetLink,
    District,
    DistrictTierBand,
    Ruleset,
    ScoutRun,
    SignalQueue,
    TerritoryConfig,
)
from artemis.marketing.state_machine import WorkspaceState

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
