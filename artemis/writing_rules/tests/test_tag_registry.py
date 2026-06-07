"""Tests for the Writing Studio tag registry slice."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import NullPool, func, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.writing_rules.models import TagDimension, TagValue
from artemis.writing_rules.tag_registry_seed import seed_tag_registry_async

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)


async def _seed_registry(session: AsyncSession) -> None:
    await seed_tag_registry_async(session)
    await session.commit()


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await _seed_registry(db_session)
    await _seed_registry(db_session)

    dimensions = await db_session.execute(select(func.count()).select_from(TagDimension))
    values = await db_session.execute(select(func.count()).select_from(TagValue))

    assert dimensions.scalar_one() == 5
    assert values.scalar_one() == 41


@pytest.mark.asyncio
async def test_get_registry_returns_seeded_dimensions_and_nested_subtypes(
    db_session: AsyncSession,
    client: AsyncClient,
) -> None:
    await _seed_registry(db_session)

    response = await client.get("/api/writing-studio/tags")
    assert response.status_code == 200, response.text

    payload = response.json()
    assert [dimension["key"] for dimension in payload["dimensions"]] == [
        "asset_type",
        "audience",
        "platform",
        "intent",
        "format",
    ]

    asset_type = payload["dimensions"][0]
    email_sequence = next(
        value for value in asset_type["values"] if value["value"] == "email sequence"
    )
    long_form = next(value for value in asset_type["values"] if value["value"] == "long form")
    parent = next(
        value for value in payload["dimensions"][1]["values"] if value["value"] == "parent"
    )

    assert [child["value"] for child in email_sequence["children"]] == [
        "welcome/onboarding",
        "nurture",
        "re-engagement/win-back",
        "event",
        "demo or meeting follow-up",
        "renewal/expansion",
        "back-to-school/seasonal",
    ]
    assert [child["value"] for child in long_form["children"]] == [
        "Decision Guide",
        "Funding Guide",
        "Field Guide",
        "Product Explainer/Overview",
    ]
    assert parent["metadata"] == {"applicable_platforms": ["social"]}


@pytest.mark.asyncio
async def test_post_dimension_and_patch_dimension_work(client: AsyncClient) -> None:
    create = await client.post(
        "/api/writing-studio/tags/dimensions",
        json={"key": "persona_stage", "label": "Persona Stage"},
    )
    assert create.status_code == 201, create.text
    assert create.json()["key"] == "persona_stage"

    patch = await client.patch(
        "/api/writing-studio/tags/dimensions/persona_stage",
        json={"label": "Persona Stage Updated", "active": False},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["label"] == "Persona Stage Updated"
    assert patch.json()["active"] is False

    registry = await client.get("/api/writing-studio/tags")
    keys = [dimension["key"] for dimension in registry.json()["dimensions"]]
    assert "persona_stage" not in keys


@pytest.mark.asyncio
async def test_post_value_then_lossless_deactivate_hides_from_get(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry(db_session)

    create = await client.post(
        "/api/writing-studio/tags/values",
        json={
            "dimensionKey": "platform",
            "value": "podcast",
            "label": "podcast",
            "metadata": {"channel_family": "audio"},
        },
    )
    assert create.status_code == 201, create.text
    created = create.json()
    assert created["metadata"] == {"channel_family": "audio"}

    registry = await client.get("/api/writing-studio/tags")
    platform = next(
        dimension for dimension in registry.json()["dimensions"] if dimension["key"] == "platform"
    )
    assert [value["value"] for value in platform["values"]][-1] == "podcast"

    patch = await client.patch(
        f"/api/writing-studio/tags/values/{created['id']}",
        json={"active": False},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["active"] is False

    registry_after = await client.get("/api/writing-studio/tags")
    platform_after = next(
        dimension
        for dimension in registry_after.json()["dimensions"]
        if dimension["key"] == "platform"
    )
    assert "podcast" not in [value["value"] for value in platform_after["values"]]

    row = await db_session.get(TagValue, created["id"])
    assert row is not None
    assert row.active is False
    assert row.value == "podcast"


@pytest.mark.asyncio
async def test_duplicate_value_returns_conflict(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry(db_session)
    response = await client.post(
        "/api/writing-studio/tags/values",
        json={
            "dimensionKey": "platform",
            "value": "email",
            "label": "email",
        },
    )
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "tag_value_conflict"


@pytest.mark.asyncio
async def test_parent_value_must_exist(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _seed_registry(db_session)
    response = await client.post(
        "/api/writing-studio/tags/values",
        json={
            "dimensionKey": "asset_type",
            "value": "unsupported child",
            "label": "unsupported child",
            "parentValue": "does not exist",
        },
    )
    assert response.status_code == 400, response.text
    assert response.json()["code"] == "tag_value_invalid"


@pytest.mark.asyncio
async def test_migration_0070_upgrade_downgrade_roundtrip() -> None:
    downgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0069"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "ARTEMIS_DB_URL": _DB_URL},
        check=False,
    )
    assert downgrade.returncode == 0, downgrade.stderr

    engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
        assert "tag_dimensions" not in tables
        assert "tag_values" not in tables
    finally:
        await engine.dispose()

    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "ARTEMIS_DB_URL": _DB_URL},
        check=False,
    )
    assert upgrade.returncode == 0, upgrade.stderr

    engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            dimension_count = await conn.scalar(select(func.count()).select_from(TagDimension))
            value_count = await conn.scalar(select(func.count()).select_from(TagValue))
        assert "tag_dimensions" in tables
        assert "tag_values" in tables
        assert dimension_count == 5
        assert value_count == 41
    finally:
        await engine.dispose()
