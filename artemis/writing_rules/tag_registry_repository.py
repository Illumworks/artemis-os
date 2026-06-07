"""Repository helpers for the Writing Studio tag registry."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.writing_rules.models import TagDimension, TagValue
from artemis.writing_rules.repository import StructuredTags, normalize_structured_tags


class TagRegistryConflictError(ValueError):
    """Raised when a create/update would violate a registry uniqueness rule."""


class TagRegistryValidationError(ValueError):
    """Raised when a requested parent/dimension relationship is invalid."""


async def validate_structured_tags(session: AsyncSession, tags: Any) -> StructuredTags:
    if not isinstance(tags, dict):
        raise TagRegistryValidationError("tags must be an object")

    dimensions = await list_tag_dimensions(session)
    allowed_values = await list_tag_values(session)
    allowed_by_dimension: dict[str, set[str]] = {dimension.key: set() for dimension in dimensions}
    for row in allowed_values:
        allowed_by_dimension.setdefault(row.dimension_key, set()).add(row.value)

    normalized = normalize_structured_tags(tags)
    validated: StructuredTags = {}
    for raw_dimension, raw_value in tags.items():
        if not isinstance(raw_dimension, str) or not raw_dimension.strip():
            raise TagRegistryValidationError("tag dimension keys must be non-empty strings")
        dimension = raw_dimension.strip()
        if dimension not in allowed_by_dimension:
            raise TagRegistryValidationError(f"Unknown tag dimension '{dimension}'")

        if isinstance(raw_value, str):
            value = raw_value.strip()
            if not value:
                raise TagRegistryValidationError(
                    f"Tag dimension '{dimension}' must be a non-empty string or non-empty string list"
                )
            if value not in allowed_by_dimension[dimension]:
                raise TagRegistryValidationError(
                    f"Unknown tag value '{value}' for dimension '{dimension}'"
                )
            validated[dimension] = normalized[dimension]
            continue

        if isinstance(raw_value, list):
            values = normalized.get(dimension)
            if not isinstance(values, list) or not values:
                raise TagRegistryValidationError(
                    f"Tag dimension '{dimension}' must be a non-empty string or non-empty string list"
                )
            invalid = [value for value in values if value not in allowed_by_dimension[dimension]]
            if invalid:
                raise TagRegistryValidationError(
                    f"Unknown tag value '{invalid[0]}' for dimension '{dimension}'"
                )
            validated[dimension] = values
            continue

        raise TagRegistryValidationError(
            f"Tag dimension '{dimension}' must be a string or list of strings"
        )

    return validated


async def list_tag_dimensions(
    session: AsyncSession, *, include_inactive: bool = False
) -> list[TagDimension]:
    stmt = select(TagDimension).order_by(TagDimension.sort_order, TagDimension.id)
    if not include_inactive:
        stmt = stmt.where(TagDimension.active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars())


async def get_tag_dimension(session: AsyncSession, key: str) -> TagDimension | None:
    result = await session.execute(select(TagDimension).where(TagDimension.key == key).limit(1))
    return result.scalar_one_or_none()


async def create_tag_dimension(session: AsyncSession, *, key: str, label: str) -> TagDimension:
    existing = await get_tag_dimension(session, key)
    if existing is not None:
        raise TagRegistryConflictError(f"Tag dimension '{key}' already exists")

    result = await session.execute(select(func.max(TagDimension.sort_order)))
    next_sort = (result.scalar_one() or 0) + 10

    row = TagDimension(key=key, label=label, sort_order=next_sort, active=True)
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_tag_dimension(
    session: AsyncSession,
    key: str,
    *,
    label: str | None = None,
    active: bool | None = None,
) -> TagDimension | None:
    row = await get_tag_dimension(session, key)
    if row is None:
        return None
    if label is not None:
        row.label = label
    if active is not None:
        row.active = active
    await session.flush()
    return row


async def list_tag_values(
    session: AsyncSession,
    *,
    dimension_key: str | None = None,
    include_inactive: bool = False,
) -> list[TagValue]:
    stmt = select(TagValue).order_by(
        TagValue.dimension_key,
        TagValue.parent_value.nullsfirst(),
        TagValue.sort_order,
        TagValue.id,
    )
    if dimension_key is not None:
        stmt = stmt.where(TagValue.dimension_key == dimension_key)
    if not include_inactive:
        stmt = stmt.where(TagValue.active.is_(True))
    result = await session.execute(stmt)
    return list(result.scalars())


async def get_tag_value(session: AsyncSession, value_id: int) -> TagValue | None:
    return await session.get(TagValue, value_id)


async def _parent_exists(session: AsyncSession, *, dimension_key: str, value: str) -> bool:
    result = await session.execute(
        select(TagValue.id)
        .where(
            TagValue.dimension_key == dimension_key,
            TagValue.value == value,
            TagValue.parent_value.is_(None),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


async def create_tag_value(
    session: AsyncSession,
    *,
    dimension_key: str,
    value: str,
    label: str,
    parent_value: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> TagValue:
    dimension = await get_tag_dimension(session, dimension_key)
    if dimension is None:
        raise TagRegistryValidationError(f"Unknown tag dimension '{dimension_key}'")
    if parent_value is not None and not await _parent_exists(
        session, dimension_key=dimension_key, value=parent_value
    ):
        raise TagRegistryValidationError(
            f"Unknown parent value '{parent_value}' for dimension '{dimension_key}'"
        )

    parent_clause = (
        TagValue.parent_value.is_(None)
        if parent_value is None
        else TagValue.parent_value == parent_value
    )
    result = await session.execute(
        select(func.max(TagValue.sort_order)).where(
            TagValue.dimension_key == dimension_key,
            parent_clause,
        )
    )
    next_sort = (result.scalar_one() or 0) + 10

    row = TagValue(
        dimension_key=dimension_key,
        value=value,
        label=label,
        parent_value=parent_value,
        sort_order=next_sort,
        active=True,
        value_metadata=metadata or {},
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise TagRegistryConflictError(
            f"Tag value '{value}' already exists for dimension '{dimension_key}'"
        ) from exc
    await session.refresh(row)
    return row


async def update_tag_value(
    session: AsyncSession,
    value_id: int,
    *,
    label: str | None = None,
    active: bool | None = None,
    sort_order: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> TagValue | None:
    row = await get_tag_value(session, value_id)
    if row is None:
        return None
    if label is not None:
        row.label = label
    if active is not None:
        row.active = active
    if sort_order is not None:
        row.sort_order = sort_order
    if metadata is not None:
        row.value_metadata = metadata
    await session.flush()
    return row


async def build_tag_registry_snapshot(
    session: AsyncSession, *, active_only: bool = True
) -> list[dict[str, Any]]:
    dimensions = await list_tag_dimensions(session, include_inactive=not active_only)
    if not dimensions:
        return []

    dimension_keys = [row.key for row in dimensions]
    stmt = select(TagValue).where(TagValue.dimension_key.in_(dimension_keys))
    if active_only:
        stmt = stmt.where(TagValue.active.is_(True))
    stmt = stmt.order_by(
        TagValue.dimension_key,
        TagValue.parent_value.nullsfirst(),
        TagValue.sort_order,
        TagValue.id,
    )
    result = await session.execute(stmt)
    values = list(result.scalars())

    values_by_dimension: dict[str, list[TagValue]] = defaultdict(list)
    for row in values:
        values_by_dimension[row.dimension_key].append(row)

    snapshot: list[dict[str, Any]] = []
    for dimension in dimensions:
        roots: list[dict[str, Any]] = []
        children_by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for row in values_by_dimension.get(dimension.key, []):
            serialized = serialize_tag_value(row)
            if row.parent_value is None:
                roots.append(serialized)
            else:
                children_by_parent[row.parent_value].append(serialized)

        for root in roots:
            root["children"] = children_by_parent.get(root["value"], [])

        snapshot.append(
            {
                "id": dimension.id,
                "key": dimension.key,
                "label": dimension.label,
                "active": dimension.active,
                "sortOrder": dimension.sort_order,
                "createdAt": dimension.created_at,
                "values": roots,
            }
        )
    return snapshot


def serialize_tag_value(row: TagValue) -> dict[str, Any]:
    return {
        "id": row.id,
        "dimensionKey": row.dimension_key,
        "value": row.value,
        "label": row.label,
        "parentValue": row.parent_value,
        "active": row.active,
        "sortOrder": row.sort_order,
        "metadata": row.value_metadata or {},
        "createdAt": row.created_at,
        "children": [],
    }
