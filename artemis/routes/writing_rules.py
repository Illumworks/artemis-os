"""Writing Studio rules router — /api/writing-rules.

Endpoints:
  GET    /api/writing-rules/profiles               — list profiles
  POST   /api/writing-rules/profiles               — create profile
  GET    /api/writing-rules/profiles/{id}          — get profile
  PATCH  /api/writing-rules/profiles/{id}          — update profile

  GET    /api/writing-rules/folders                — list folders (optional ?profileId=)
  POST   /api/writing-rules/folders                — create folder
  GET    /api/writing-rules/folders/{id}           — get folder
  PATCH  /api/writing-rules/folders/{id}           — update folder
  DELETE /api/writing-rules/folders/{id}           — delete folder

  GET    /api/writing-rules/rules                  — list rules (optional ?profileId=&ruleType=)
  POST   /api/writing-rules/rules                  — create rule
  POST   /api/writing-rules/rules/resolve          — resolve matching rules for tags
  GET    /api/writing-rules/rules/{id}             — get rule
  PATCH  /api/writing-rules/rules/{id}             — update rule
  DELETE /api/writing-rules/rules/{id}             — delete rule

  GET    /api/writing-rules/examples               — list examples (optional ?profileId=)
  POST   /api/writing-rules/examples               — create example
  GET    /api/writing-rules/examples/{id}          — get example
  PATCH  /api/writing-rules/examples/{id}          — update example
  DELETE /api/writing-rules/examples/{id}          — delete example

  GET    /api/writing-rules/sources                — list sources (optional ?profileId=)
  POST   /api/writing-rules/sources                — create source
  GET    /api/writing-rules/sources/{id}           — get source
  PATCH  /api/writing-rules/sources/{id}           — update source
  DELETE /api/writing-rules/sources/{id}           — delete source
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.writing_rules import repository as repo
from artemis.writing_rules.schemas import (
    WritingExampleCreate,
    WritingExampleRead,
    WritingExampleUpdate,
    WritingFolderCreate,
    WritingFolderRead,
    WritingFolderUpdate,
    WritingProfileCreate,
    WritingProfileRead,
    WritingProfileUpdate,
    WritingRuleCreate,
    WritingRuleRead,
    WritingRuleResolveRequest,
    WritingRuleUpdate,
    WritingSourceCreate,
    WritingSourceRead,
    WritingSourceUpdate,
)

router = APIRouter(
    prefix="/api/writing-rules",
    tags=["writing-rules"],
    dependencies=[Depends(require_token)],
)


def _not_found(label: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": f"{label} not found", "code": "not_found"},
    )


# ── Profiles ──────────────────────────────────────────────────────────────────


@router.get("/profiles", response_model=list[WritingProfileRead])
async def list_profiles(
    include_archived: bool = False,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WritingProfileRead]:
    rows = await repo.list_profiles(session, include_archived=include_archived)
    return [WritingProfileRead.model_validate(r) for r in rows]


@router.post("/profiles", response_model=WritingProfileRead, status_code=201)
async def create_profile(
    body: WritingProfileCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingProfileRead:
    profile = await repo.create_profile(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(profile)
    return WritingProfileRead.model_validate(profile)


@router.get("/profiles/{profile_id}", response_model=WritingProfileRead)
async def get_profile(
    profile_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingProfileRead:
    profile = await repo.get_profile(session, profile_id)
    if profile is None:
        raise _not_found("Profile")
    return WritingProfileRead.model_validate(profile)


@router.patch("/profiles/{profile_id}", response_model=WritingProfileRead)
async def update_profile(
    profile_id: int,
    body: WritingProfileUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingProfileRead:
    profile = await repo.update_profile(session, profile_id, **body.model_dump(exclude_none=True))
    if profile is None:
        raise _not_found("Profile")
    await session.commit()
    await session.refresh(profile)
    return WritingProfileRead.model_validate(profile)


# ── Folders ───────────────────────────────────────────────────────────────────


@router.get("/folders", response_model=list[WritingFolderRead])
async def list_folders(
    profile_id: int | None = Query(default=None, alias="profileId"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WritingFolderRead]:
    rows = await repo.list_folders(session, profile_id=profile_id)
    return [WritingFolderRead.model_validate(r) for r in rows]


@router.post("/folders", response_model=WritingFolderRead, status_code=201)
async def create_folder(
    body: WritingFolderCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingFolderRead:
    folder = await repo.create_folder(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(folder)
    return WritingFolderRead.model_validate(folder)


@router.get("/folders/{folder_id}", response_model=WritingFolderRead)
async def get_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingFolderRead:
    folder = await repo.get_folder(session, folder_id)
    if folder is None:
        raise _not_found("Folder")
    return WritingFolderRead.model_validate(folder)


@router.patch("/folders/{folder_id}", response_model=WritingFolderRead)
async def update_folder(
    folder_id: int,
    body: WritingFolderUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingFolderRead:
    folder = await repo.update_folder(session, folder_id, **body.model_dump(exclude_none=True))
    if folder is None:
        raise _not_found("Folder")
    await session.commit()
    await session.refresh(folder)
    return WritingFolderRead.model_validate(folder)


@router.delete("/folders/{folder_id}", status_code=204)
async def delete_folder(
    folder_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_folder(session, folder_id)
    if not deleted:
        raise _not_found("Folder")
    await session.commit()


# ── Rules ─────────────────────────────────────────────────────────────────────


@router.get("/rules", response_model=list[WritingRuleRead])
async def list_rules(
    profile_id: int | None = Query(default=None, alias="profileId"),
    rule_type: str | None = Query(default=None, alias="ruleType"),
    include_archived: bool = False,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WritingRuleRead]:
    rows = await repo.list_rules(
        session,
        profile_id=profile_id,
        rule_type=rule_type,
        include_archived=include_archived,
    )
    return [WritingRuleRead.model_validate(r) for r in rows]


@router.post("/rules", response_model=WritingRuleRead, status_code=201)
async def create_rule(
    body: WritingRuleCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingRuleRead:
    rule = await repo.create_rule(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(rule)
    return WritingRuleRead.model_validate(rule)


@router.post("/rules/resolve", response_model=list[WritingRuleRead])
async def resolve_rules(
    body: WritingRuleResolveRequest,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WritingRuleRead]:
    rows = await repo.resolve_rules_for_tags(
        session,
        profile_id=body.profile_id,
        tags=body.tags,
    )
    return [WritingRuleRead.model_validate(row) for row in rows]


@router.get("/rules/{rule_id}", response_model=WritingRuleRead)
async def get_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingRuleRead:
    rule = await repo.get_rule(session, rule_id)
    if rule is None:
        raise _not_found("Rule")
    return WritingRuleRead.model_validate(rule)


@router.patch("/rules/{rule_id}", response_model=WritingRuleRead)
async def update_rule(
    rule_id: int,
    body: WritingRuleUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingRuleRead:
    rule = await repo.update_rule(session, rule_id, **body.model_dump(exclude_none=True))
    if rule is None:
        raise _not_found("Rule")
    await session.commit()
    await session.refresh(rule)
    return WritingRuleRead.model_validate(rule)


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_rule(session, rule_id)
    if not deleted:
        raise _not_found("Rule")
    await session.commit()


# ── Examples ──────────────────────────────────────────────────────────────────


@router.get("/examples", response_model=list[WritingExampleRead])
async def list_examples(
    profile_id: int | None = Query(default=None, alias="profileId"),
    example_type: str | None = Query(default=None, alias="exampleType"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WritingExampleRead]:
    rows = await repo.list_examples(session, profile_id=profile_id, example_type=example_type)
    return [WritingExampleRead.model_validate(r) for r in rows]


@router.post("/examples", response_model=WritingExampleRead, status_code=201)
async def create_example(
    body: WritingExampleCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingExampleRead:
    example = await repo.create_example(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(example)
    return WritingExampleRead.model_validate(example)


@router.get("/examples/{example_id}", response_model=WritingExampleRead)
async def get_example(
    example_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingExampleRead:
    example = await repo.get_example(session, example_id)
    if example is None:
        raise _not_found("Example")
    return WritingExampleRead.model_validate(example)


@router.patch("/examples/{example_id}", response_model=WritingExampleRead)
async def update_example(
    example_id: int,
    body: WritingExampleUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingExampleRead:
    example = await repo.update_example(session, example_id, **body.model_dump(exclude_none=True))
    if example is None:
        raise _not_found("Example")
    await session.commit()
    await session.refresh(example)
    return WritingExampleRead.model_validate(example)


@router.delete("/examples/{example_id}", status_code=204)
async def delete_example(
    example_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_example(session, example_id)
    if not deleted:
        raise _not_found("Example")
    await session.commit()


# ── Sources ───────────────────────────────────────────────────────────────────


@router.get("/sources", response_model=list[WritingSourceRead])
async def list_sources(
    profile_id: int | None = Query(default=None, alias="profileId"),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[WritingSourceRead]:
    rows = await repo.list_sources(session, profile_id=profile_id)
    return [WritingSourceRead.model_validate(r) for r in rows]


@router.post("/sources", response_model=WritingSourceRead, status_code=201)
async def create_source(
    body: WritingSourceCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingSourceRead:
    source = await repo.create_source(session, **body.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(source)
    return WritingSourceRead.model_validate(source)


@router.get("/sources/{source_id}", response_model=WritingSourceRead)
async def get_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingSourceRead:
    source = await repo.get_source(session, source_id)
    if source is None:
        raise _not_found("Source")
    return WritingSourceRead.model_validate(source)


@router.patch("/sources/{source_id}", response_model=WritingSourceRead)
async def update_source(
    source_id: int,
    body: WritingSourceUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> WritingSourceRead:
    source = await repo.update_source(session, source_id, **body.model_dump(exclude_none=True))
    if source is None:
        raise _not_found("Source")
    await session.commit()
    await session.refresh(source)
    return WritingSourceRead.model_validate(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> None:
    deleted = await repo.delete_source(session, source_id)
    if not deleted:
        raise _not_found("Source")
    await session.commit()
