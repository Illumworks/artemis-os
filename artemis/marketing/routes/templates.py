"""Structured templates management API."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.marketing.writing_studio import invoke as ws_invoke
from artemis.writing_rules import repository as wr_repo
from artemis.writing_rules.schemas import (
    AppliedDraftRead,
    TemplateApplyRequest,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
)

TemplateStatus = Literal["active", "retired"]

router = APIRouter(
    prefix="/api/writing-studio/templates",
    tags=["writing-studio-templates"],
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


@router.get("", response_model=list[TemplateRead])
async def list_templates(
    profile_id: int | None = Query(default=None, alias="profileId"),  # noqa: B008
    status: TemplateStatus | None = Query(default=None),  # noqa: B008
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[TemplateRead]:
    resolved_profile_id = await _resolve_profile_id(session, profile_id, required=False)
    if resolved_profile_id is None:
        return []
    templates = await wr_repo.list_templates(session, resolved_profile_id, status=status)
    return [TemplateRead.model_validate(template) for template in templates]


@router.get("/{template_id}", response_model=TemplateRead)
async def get_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TemplateRead:
    template = await wr_repo.get_template(session, template_id)
    if template is None:
        raise not_found("template not found", "template_not_found")
    return TemplateRead.model_validate(template)


@router.post("", response_model=TemplateRead, status_code=201)
async def create_template(
    body: TemplateCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TemplateRead:
    resolved_profile_id = await _resolve_profile_id(session, body.profile_id, required=True)
    assert resolved_profile_id is not None
    template = await wr_repo.create_template(
        session,
        profile_id=resolved_profile_id,
        template_key=body.template_key,
        name=body.name,
        asset_type=body.asset_type,
        body=body.body,
        superseded_by=body.superseded_by,
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(
            "templateKey must be unique within a profile",
            "template_conflict",
        ) from exc
    await session.refresh(template)
    return TemplateRead.model_validate(template)


@router.patch("/{template_id}", response_model=TemplateRead)
async def update_template(
    template_id: int,
    body: TemplateUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TemplateRead:
    template = await wr_repo.update_template(
        session,
        template_id,
        **body.model_dump(exclude_unset=True),
    )
    if template is None:
        raise not_found("template not found", "template_not_found")
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise conflict(
            "templateKey must be unique within a profile",
            "template_conflict",
        ) from exc
    await session.refresh(template)
    return TemplateRead.model_validate(template)


@router.post("/{template_id}/retire", response_model=TemplateRead)
async def retire_template(
    template_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TemplateRead:
    template = await wr_repo.retire_template(session, template_id)
    if template is None:
        raise not_found("template not found", "template_not_found")
    await session.commit()
    await session.refresh(template)
    return TemplateRead.model_validate(template)


@router.post("/{template_id}/apply", response_model=AppliedDraftRead, status_code=201)
async def apply_template(
    template_id: int,
    body: TemplateApplyRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> AppliedDraftRead:
    template = await wr_repo.get_template(session, template_id)
    if template is None:
        raise not_found("template not found", "template_not_found")
    if template.status != "active":
        raise conflict("template must be active to apply", "template_inactive")
    if body.title is not None and not body.title.strip():
        raise bad_request("title must be a non-empty string", "invalid_title")

    if body.folder_id is not None:
        folder = await wr_repo.get_folder(session, body.folder_id)
        if folder is None:
            raise not_found("folder not found", "folder_not_found")

    draft = await ws_invoke.create_template_draft(
        session,
        profile_id=template.profile_id,
        template_id=template.id,
        template_key=template.template_key,
        template_name=template.name,
        template_body=template.body,
        title=(body.title or template.name).strip(),
        folder_id=body.folder_id,
    )

    return AppliedDraftRead(
        id=draft.id,
        candidate_id=draft.candidate_id,
        title=draft.title,
        status=draft.status,
        folder_id=draft.metadata.get("folder_id"),
        content=template.body,
        created_at=draft.created_at,
    )
