"""Claims Register management API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.schemas import ClaimCreate, ClaimRead, ClaimUpdate

ClaimStatus = Literal["proposed", "approved", "retired"]

router = APIRouter(
    prefix="/api/writing-studio/claims",
    tags=["writing-studio-claims"],
    dependencies=[Depends(require_token)],
)


async def _resolve_profile_id(
    session: AsyncSession,
    profile_id: int | None,
    *,
    required: bool,
) -> int | None:
    if profile_id is not None:
        return profile_id

    active_profile = await wr_repo.get_active_profile(session)
    if active_profile is None:
        if required:
            raise bad_request(
                "profileId is required when no active profile exists", "profile_missing"
            )
        return None
    return active_profile.id


@router.get("", response_model=list[ClaimRead])
async def list_claims(
    profile_id: int | None = Query(default=None, alias="profileId"),  # noqa: B008
    status: ClaimStatus | None = Query(default=None),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[ClaimRead]:
    resolved_profile_id = await _resolve_profile_id(session, profile_id, required=False)
    if resolved_profile_id is None:
        return []
    claims = await wr_repo.list_claims(session, resolved_profile_id, status=status)
    return [ClaimRead.model_validate(claim) for claim in claims]


@router.get("/{claim_id}", response_model=ClaimRead)
async def get_claim(
    claim_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ClaimRead:
    claim = await wr_repo.get_claim(session, claim_id)
    if claim is None:
        raise not_found("claim not found", "claim_not_found")
    return ClaimRead.model_validate(claim)


@router.post("", response_model=ClaimRead, status_code=201)
async def create_claim(
    body: ClaimCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ClaimRead:
    resolved_profile_id = await _resolve_profile_id(session, body.profile_id, required=True)
    assert resolved_profile_id is not None
    claim = await wr_repo.create_claim(
        session,
        profile_id=resolved_profile_id,
        claim_code=body.claim_code,
        category=body.category,
        tier=body.tier,
        approved_phrasing=body.approved_phrasing,
        packaging=body.packaging,
        notes=body.notes,
        source=body.source,
        superseded_by=body.superseded_by,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict("claimCode must be unique within a profile", "claim_conflict") from exc
    await session.refresh(claim)
    return ClaimRead.model_validate(claim)


@router.patch("/{claim_id}", response_model=ClaimRead)
async def update_claim(
    claim_id: int,
    body: ClaimUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ClaimRead:
    claim = await wr_repo.update_claim(session, claim_id, **body.model_dump(exclude_unset=True))
    if claim is None:
        raise not_found("claim not found", "claim_not_found")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict("claimCode must be unique within a profile", "claim_conflict") from exc
    await session.refresh(claim)
    return ClaimRead.model_validate(claim)


@router.post("/{claim_id}/approve", response_model=ClaimRead)
async def approve_claim(
    claim_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ClaimRead:
    try:
        claim = await wr_repo.approve_claim(session, claim_id)
    except ValueError as exc:
        raise conflict(str(exc), "claim_transition_invalid") from exc
    if claim is None:
        raise not_found("claim not found", "claim_not_found")
    await session.commit()
    await session.refresh(claim)
    return ClaimRead.model_validate(claim)


@router.post("/{claim_id}/retire", response_model=ClaimRead)
async def retire_claim(
    claim_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> ClaimRead:
    claim = await wr_repo.retire_claim(session, claim_id)
    if claim is None:
        raise not_found("claim not found", "claim_not_found")
    await session.commit()
    await session.refresh(claim)
    return ClaimRead.model_validate(claim)
