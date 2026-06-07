"""Tests for the Phase 2 tag-scoped writing rules engine."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import NullPool, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.writing_rules import repository as repo
from artemis.writing_rules.models import WritingRule

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)


async def _create_profile(session: AsyncSession, name: str = "District Voice") -> int:
    profile = await repo.create_profile(session, name=name, status="active")
    await session.commit()
    return profile.id


async def _seed_scope_matrix_rules(session: AsyncSession, profile_id: int) -> None:
    rules = [
        {
            "title": "Global brand voice",
            "body": "Applies everywhere.",
            "tag_scope": {},
        },
        {
            "title": "Superintendent audience",
            "body": "Use district-leader framing.",
            "tag_scope": {"audience": ["superintendent"]},
        },
        {
            "title": "Audience OR board",
            "body": "Allowed for superintendent or board member.",
            "tag_scope": {"audience": ["superintendent", "board member"]},
        },
        {
            "title": "Superintendent email",
            "body": "Only for superintendent emails.",
            "tag_scope": {
                "audience": ["superintendent"],
                "platform": ["email"],
            },
        },
        {
            "title": "Newsletter format",
            "body": "Only for newsletter format assets.",
            "tag_scope": {"format": ["newsletter"]},
        },
    ]
    for rule in rules:
        await repo.create_rule(
            session,
            profile_id=profile_id,
            rule_type="voice",
            status="active",
            **rule,
        )
    await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "tags", "expected_titles"),
    [
        (
            "global_only",
            {"audience": "teacher"},
            ["Global brand voice"],
        ),
        (
            "single_dimension",
            {"audience": "superintendent"},
            [
                "Global brand voice",
                "Superintendent audience",
                "Audience OR board",
            ],
        ),
        (
            "multi_dimension_and",
            {"audience": "superintendent", "platform": "email"},
            [
                "Global brand voice",
                "Superintendent audience",
                "Audience OR board",
                "Superintendent email",
            ],
        ),
        (
            "or_within_dimension",
            {"audience": "board member"},
            [
                "Global brand voice",
                "Audience OR board",
            ],
        ),
        (
            "missing_dimension_blocks_match",
            {"audience": "superintendent", "platform": "social"},
            [
                "Global brand voice",
                "Superintendent audience",
                "Audience OR board",
            ],
        ),
    ],
)
async def test_resolve_rules_for_tags_matrix(
    db_session: AsyncSession,
    case_name: str,
    tags: dict[str, str],
    expected_titles: list[str],
) -> None:
    profile_id = await _create_profile(db_session, name=f"Profile {case_name}")
    await _seed_scope_matrix_rules(db_session, profile_id)

    resolved = await repo.resolve_rules_for_tags(db_session, profile_id, tags)

    assert [rule.title for rule in resolved] == expected_titles


@pytest.mark.asyncio
async def test_rules_create_update_and_get_roundtrip_tag_scope(client: AsyncClient) -> None:
    profile_response = await client.post(
        "/api/writing-rules/profiles",
        json={"name": "Resolve API Profile"},
    )
    assert profile_response.status_code == 201, profile_response.text
    profile_id = profile_response.json()["id"]

    create_response = await client.post(
        "/api/writing-rules/rules",
        json={
            "profileId": profile_id,
            "ruleType": "voice",
            "title": "Scoped email rule",
            "body": "Only applies to superintendent email.",
            "tagScope": {
                "audience": ["superintendent"],
                "platform": ["email"],
            },
        },
    )
    assert create_response.status_code == 201, create_response.text
    created = create_response.json()
    assert created["tagScope"] == {
        "audience": ["superintendent"],
        "platform": ["email"],
    }

    patch_response = await client.patch(
        f"/api/writing-rules/rules/{created['id']}",
        json={
            "tagScope": {
                "audience": ["superintendent", "board member"],
                "platform": ["email"],
            }
        },
    )
    assert patch_response.status_code == 200, patch_response.text
    assert patch_response.json()["tagScope"] == {
        "audience": ["superintendent", "board member"],
        "platform": ["email"],
    }

    get_response = await client.get(f"/api/writing-rules/rules/{created['id']}")
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["tagScope"] == {
        "audience": ["superintendent", "board member"],
        "platform": ["email"],
    }


@pytest.mark.asyncio
async def test_resolve_endpoint_returns_matching_rules(client: AsyncClient) -> None:
    profile_response = await client.post(
        "/api/writing-rules/profiles",
        json={"name": "Resolver Profile"},
    )
    assert profile_response.status_code == 201, profile_response.text
    profile_id = profile_response.json()["id"]

    rule_payloads = [
        {
            "title": "Global rule",
            "body": "Always applies.",
            "tagScope": {},
        },
        {
            "title": "Superintendent email rule",
            "body": "Applies only for superintendent email.",
            "tagScope": {
                "audience": ["superintendent"],
                "platform": ["email"],
            },
        },
        {
            "title": "Board social rule",
            "body": "Only for board social posts.",
            "tagScope": {
                "audience": ["board member"],
                "platform": ["social"],
            },
        },
    ]
    for payload in rule_payloads:
        response = await client.post(
            "/api/writing-rules/rules",
            json={
                "profileId": profile_id,
                "ruleType": "voice",
                "status": "active",
                **payload,
            },
        )
        assert response.status_code == 201, response.text

    resolve_response = await client.post(
        "/api/writing-rules/rules/resolve",
        json={
            "profileId": profile_id,
            "tags": {
                "audience": "superintendent",
                "platform": "email",
            },
        },
    )
    assert resolve_response.status_code == 200, resolve_response.text
    assert [rule["title"] for rule in resolve_response.json()] == [
        "Global rule",
        "Superintendent email rule",
    ]


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "ARTEMIS_DB_URL": _DB_URL},
        check=False,
    )


@pytest.mark.asyncio
async def test_migration_0071_roundtrip_preserves_existing_rules_as_global() -> None:
    downgrade = _run_alembic("downgrade", "0070")
    assert downgrade.returncode == 0, downgrade.stderr

    try:
        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text("TRUNCATE writing_rules, writing_profiles RESTART IDENTITY CASCADE")
                )
                await conn.execute(
                    text(
                        """
                        INSERT INTO writing_profiles (id, name, status, created_at, updated_at)
                        VALUES (1, 'Migrated Profile', 'active', NOW(), NOW())
                        """
                    )
                )
                for index in range(1, 4):
                    await conn.execute(
                        text(
                            """
                            INSERT INTO writing_rules (
                                profile_id,
                                rule_type,
                                title,
                                body,
                                status,
                                created_at,
                                updated_at
                            )
                            VALUES (
                                1,
                                'voice',
                                :title,
                                :body,
                                'active',
                                NOW(),
                                NOW()
                            )
                            """
                        ),
                        {
                            "title": f"Seeded Rule {index}",
                            "body": f"Seeded body {index}",
                        },
                    )
        finally:
            await engine.dispose()

        upgrade = _run_alembic("upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr

        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                columns = await conn.run_sync(
                    lambda sync_conn: {
                        column["name"] for column in inspect(sync_conn).get_columns("writing_rules")
                    }
                )
            assert "tag_scope" in columns

            async with AsyncSession(engine, expire_on_commit=False) as session:
                result = await session.execute(
                    select(WritingRule).where(WritingRule.profile_id == 1).order_by(WritingRule.id)
                )
                rules = list(result.scalars())
                assert [rule.tag_scope for rule in rules] == [{}, {}, {}]

                resolved = await repo.resolve_rules_for_tags(
                    session,
                    1,
                    {"audience": "teacher", "platform": "social"},
                )
                assert [rule.title for rule in resolved] == [
                    "Seeded Rule 1",
                    "Seeded Rule 2",
                    "Seeded Rule 3",
                ]
        finally:
            await engine.dispose()

        downgrade_again = _run_alembic("downgrade", "0070")
        assert downgrade_again.returncode == 0, downgrade_again.stderr

        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                columns_after = await conn.run_sync(
                    lambda sync_conn: {
                        column["name"] for column in inspect(sync_conn).get_columns("writing_rules")
                    }
                )
            assert "tag_scope" not in columns_after
        finally:
            await engine.dispose()
    finally:
        restore = _run_alembic("upgrade", "head")
        assert restore.returncode == 0, restore.stderr
