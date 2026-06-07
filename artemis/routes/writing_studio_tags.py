"""Writing Studio tag registry router — /api/writing-studio/tags."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.db import get_session
from artemis.marketing.routes._auth import require_token
from artemis.marketing.routes._errors import bad_request, conflict, not_found
from artemis.writing_rules import tag_registry_repository as repo
from artemis.writing_rules.tag_registry_schemas import (
    TagDimensionCreate,
    TagDimensionRead,
    TagDimensionUpdate,
    TagRegistryRead,
    TagValueCreate,
    TagValueRead,
    TagValueUpdate,
)

router = APIRouter(
    prefix="/api/writing-studio/tags",
    tags=["writing-studio"],
    dependencies=[Depends(require_token)],
)


@router.get("")
@router.get("/")
async def get_tag_registry(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TagRegistryRead:
    dimensions = await repo.build_tag_registry_snapshot(session, active_only=True)
    return TagRegistryRead(dimensions=dimensions)


@router.post("/dimensions", status_code=201)
async def create_tag_dimension(
    body: TagDimensionCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TagDimensionRead:
    try:
        row = await repo.create_tag_dimension(session, key=body.key, label=body.label)
    except repo.TagRegistryConflictError as exc:
        raise conflict(str(exc), "tag_dimension_conflict") from exc  # noqa: B904
    await session.commit()
    await session.refresh(row)
    return TagDimensionRead.model_validate(
        {
            "id": row.id,
            "key": row.key,
            "label": row.label,
            "active": row.active,
            "sortOrder": row.sort_order,
            "createdAt": row.created_at,
            "values": [],
        }
    )


@router.patch("/dimensions/{key}")
async def update_tag_dimension(
    key: str,
    body: TagDimensionUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TagDimensionRead:
    row = await repo.update_tag_dimension(
        session,
        key,
        label=body.label,
        active=body.active,
    )
    if row is None:
        raise not_found(f"Tag dimension '{key}' not found", "tag_dimension_not_found")
    await session.commit()
    await session.refresh(row)
    values = await repo.build_tag_registry_snapshot(session, active_only=False)
    payload = next((dimension for dimension in values if dimension["key"] == key), None)
    if payload is None:
        raise bad_request("Tag dimension update failed", "tag_dimension_update_failed")
    return TagDimensionRead.model_validate(payload)


@router.post("/values", status_code=201)
async def create_tag_value(
    body: TagValueCreate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TagValueRead:
    try:
        row = await repo.create_tag_value(
            session,
            dimension_key=body.dimension_key,
            value=body.value,
            label=body.label,
            parent_value=body.parent_value,
            metadata=body.metadata,
        )
    except repo.TagRegistryConflictError as exc:
        raise conflict(str(exc), "tag_value_conflict") from exc  # noqa: B904
    except repo.TagRegistryValidationError as exc:
        raise bad_request(str(exc), "tag_value_invalid") from exc  # noqa: B904
    await session.commit()
    await session.refresh(row)
    return TagValueRead.model_validate(repo.serialize_tag_value(row))


@router.patch("/values/{value_id}")
async def update_tag_value(
    value_id: int,
    body: TagValueUpdate,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> TagValueRead:
    row = await repo.update_tag_value(
        session,
        value_id,
        label=body.label,
        active=body.active,
        sort_order=body.sort_order,
        metadata=body.metadata,
    )
    if row is None:
        raise not_found(f"Tag value '{value_id}' not found", "tag_value_not_found")
    await session.commit()
    await session.refresh(row)
    return TagValueRead.model_validate(repo.serialize_tag_value(row))
