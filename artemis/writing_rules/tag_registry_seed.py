"""Seed data and helpers for the Writing Studio tag registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class TagDimensionSeed:
    key: str
    label: str
    sort_order: int


@dataclass(frozen=True)
class TagValueSeed:
    dimension_key: str
    value: str
    label: str
    sort_order: int
    parent_value: str | None = None
    metadata: dict[str, Any] | None = None


TAG_DIMENSION_SEEDS: tuple[TagDimensionSeed, ...] = (
    TagDimensionSeed(key="asset_type", label="Asset Type", sort_order=10),
    TagDimensionSeed(key="audience", label="Audience", sort_order=20),
    TagDimensionSeed(key="platform", label="Platform", sort_order=30),
    TagDimensionSeed(key="intent", label="Intent", sort_order=40),
    TagDimensionSeed(key="format", label="Format", sort_order=50),
)


_FORMAT_METADATA = {
    "registry_note": "Flexible/extensible starter value; format is not a closed vocabulary.",
}


TAG_VALUE_SEEDS: tuple[TagValueSeed, ...] = (
    TagValueSeed("asset_type", "outreach email", "outreach email", 10),
    TagValueSeed("asset_type", "email sequence", "email sequence", 20),
    TagValueSeed("asset_type", "social post", "social post", 30),
    TagValueSeed("asset_type", "blog", "blog", 40),
    TagValueSeed("asset_type", "long form", "long form", 50),
    TagValueSeed("asset_type", "product paper", "product paper", 60),
    TagValueSeed("asset_type", "landing page", "landing page", 70),
    TagValueSeed("asset_type", "webpage", "webpage", 80),
    TagValueSeed("asset_type", "impact story", "impact story", 90),
    TagValueSeed(
        "asset_type",
        "welcome/onboarding",
        "welcome/onboarding",
        10,
        parent_value="email sequence",
    ),
    TagValueSeed("asset_type", "nurture", "nurture", 20, parent_value="email sequence"),
    TagValueSeed(
        "asset_type",
        "re-engagement/win-back",
        "re-engagement/win-back",
        30,
        parent_value="email sequence",
    ),
    TagValueSeed("asset_type", "event", "event", 40, parent_value="email sequence"),
    TagValueSeed(
        "asset_type",
        "demo or meeting follow-up",
        "demo or meeting follow-up",
        50,
        parent_value="email sequence",
    ),
    TagValueSeed(
        "asset_type",
        "renewal/expansion",
        "renewal/expansion",
        60,
        parent_value="email sequence",
    ),
    TagValueSeed(
        "asset_type",
        "back-to-school/seasonal",
        "back-to-school/seasonal",
        70,
        parent_value="email sequence",
    ),
    TagValueSeed("asset_type", "Decision Guide", "Decision Guide", 10, parent_value="long form"),
    TagValueSeed("asset_type", "Funding Guide", "Funding Guide", 20, parent_value="long form"),
    TagValueSeed("asset_type", "Field Guide", "Field Guide", 30, parent_value="long form"),
    TagValueSeed(
        "asset_type",
        "Product Explainer/Overview",
        "Product Explainer/Overview",
        40,
        parent_value="long form",
    ),
    TagValueSeed("audience", "superintendent", "superintendent", 10),
    TagValueSeed("audience", "district leader", "district leader", 20),
    TagValueSeed("audience", "curriculum director", "curriculum director", 30),
    TagValueSeed("audience", "principal", "principal", 40),
    TagValueSeed("audience", "board member", "board member", 50),
    TagValueSeed("audience", "special-ed director", "special-ed director", 60),
    TagValueSeed("audience", "teacher", "teacher", 70),
    TagValueSeed(
        "audience",
        "parent",
        "parent",
        80,
        metadata={"applicable_platforms": ["social"]},
    ),
    TagValueSeed("platform", "email", "email", 10),
    TagValueSeed("platform", "social", "social", 20),
    TagValueSeed("platform", "web/landing", "web/landing", 30),
    TagValueSeed("platform", "print", "print", 40),
    TagValueSeed("intent", "awareness", "awareness", 10),
    TagValueSeed("intent", "consideration", "consideration", 20),
    TagValueSeed("intent", "decision", "decision", 30),
    TagValueSeed("intent", "expansion", "expansion", 40),
    TagValueSeed("intent", "credibility/proof", "credibility/proof", 50),
    TagValueSeed("format", "one-page", "one-page", 10, metadata=_FORMAT_METADATA),
    TagValueSeed("format", "two-page", "two-page", 20, metadata=_FORMAT_METADATA),
    TagValueSeed("format", "short", "short", 30, metadata=_FORMAT_METADATA),
    TagValueSeed("format", "long", "long", 40, metadata=_FORMAT_METADATA),
)


_UPSERT_DIMENSION_SQL = text(
    """
    INSERT INTO tag_dimensions (key, label, active, sort_order)
    VALUES (:key, :label, TRUE, :sort_order)
    ON CONFLICT (key) DO UPDATE
    SET
        label = EXCLUDED.label,
        active = TRUE,
        sort_order = EXCLUDED.sort_order
    """
)

_UPSERT_VALUE_SQL = text(
    """
    INSERT INTO tag_values (
        dimension_key,
        value,
        label,
        parent_value,
        active,
        sort_order,
        metadata
    )
    VALUES (
        :dimension_key,
        :value,
        :label,
        :parent_value,
        TRUE,
        :sort_order,
        CAST(:metadata AS JSONB)
    )
    ON CONFLICT (dimension_key, value, COALESCE(parent_value, ''))
    DO UPDATE
    SET
        label = EXCLUDED.label,
        active = TRUE,
        sort_order = EXCLUDED.sort_order,
        metadata = EXCLUDED.metadata
    """
)


def _seed_rows(connection: Connection) -> None:
    for dimension in TAG_DIMENSION_SEEDS:
        connection.execute(
            _UPSERT_DIMENSION_SQL,
            {
                "key": dimension.key,
                "label": dimension.label,
                "sort_order": dimension.sort_order,
            },
        )
    for value in TAG_VALUE_SEEDS:
        connection.execute(
            _UPSERT_VALUE_SQL,
            {
                "dimension_key": value.dimension_key,
                "value": value.value,
                "label": value.label,
                "parent_value": value.parent_value,
                "sort_order": value.sort_order,
                "metadata": "{}" if value.metadata is None else json.dumps(value.metadata),
            },
        )


def seed_tag_registry_sync(connection: Connection) -> None:
    """Idempotently seed the locked tag vocabulary using a sync Connection."""
    _seed_rows(connection)


async def seed_tag_registry_async(session: AsyncSession) -> None:
    """Idempotently seed the locked tag vocabulary using an AsyncSession."""
    for dimension in TAG_DIMENSION_SEEDS:
        await session.execute(
            _UPSERT_DIMENSION_SQL,
            {
                "key": dimension.key,
                "label": dimension.label,
                "sort_order": dimension.sort_order,
            },
        )
    for value in TAG_VALUE_SEEDS:
        await session.execute(
            _UPSERT_VALUE_SQL,
            {
                "dimension_key": value.dimension_key,
                "value": value.value,
                "label": value.label,
                "parent_value": value.parent_value,
                "sort_order": value.sort_order,
                "metadata": "{}" if value.metadata is None else json.dumps(value.metadata),
            },
        )
