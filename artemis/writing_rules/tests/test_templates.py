"""Templates parser, repository, seed, and migration tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import NullPool, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.writing_rules import repository as repo
from artemis.writing_rules.seed_corpus import import_templates, parse_templates_markdown

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

_SAMPLE_TEMPLATES_MARKDOWN = """
## Template X — Launch opener
"Open with the district problem, then name the coherence mechanism."

## Template Y — Slide structure
Headline:
"The district strategy becomes classroom execution."
Bullets:
- Assess generates continual evidence.
- Instruct turns evidence into weekly plans.
- Tutor reinforces the targeted skills.
"""


async def _create_profile(session: AsyncSession, name: str = "Templates Profile") -> int:
    profile = await repo.create_profile(session, name=name, status="active")
    await session.commit()
    return profile.id


def _run_alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        env={**os.environ, "ARTEMIS_DB_URL": _DB_URL},
        check=False,
    )


def test_parse_templates_markdown_fixture() -> None:
    templates = parse_templates_markdown(_SAMPLE_TEMPLATES_MARKDOWN)

    assert len(templates) == 2
    assert templates[0].template_key == "X"
    assert templates[0].name == "Launch opener"
    assert templates[0].body == "Open with the district problem, then name the coherence mechanism."
    assert templates[0].asset_type is None

    assert templates[1].template_key == "Y"
    assert templates[1].name == "Slide structure"
    assert templates[1].body.startswith("Headline:")
    assert "Assess generates continual evidence." in templates[1].body


@pytest.mark.asyncio
async def test_import_templates_is_idempotent(db_session: AsyncSession) -> None:
    profile_id = await _create_profile(db_session)

    imported_first = await import_templates(
        db_session, profile_id, markdown=_SAMPLE_TEMPLATES_MARKDOWN
    )
    await db_session.commit()

    imported_second = await import_templates(
        db_session, profile_id, markdown=_SAMPLE_TEMPLATES_MARKDOWN
    )
    await db_session.commit()

    templates = await repo.list_templates(db_session, profile_id, status="active")
    assert imported_first == 2
    assert imported_second == 2
    assert len(templates) == 2
    assert [template.template_key for template in templates] == ["X", "Y"]


@pytest.mark.asyncio
async def test_template_repository_lifecycle_is_lossless(db_session: AsyncSession) -> None:
    profile_id = await _create_profile(db_session, name="Template Lifecycle Profile")

    template = await repo.create_template(
        db_session,
        profile_id=profile_id,
        template_key="A1",
        name="Original template",
        body="First pass body.",
    )
    await db_session.commit()
    assert template.status == "active"

    updated = await repo.update_template(
        db_session,
        template.id,
        name="Updated template",
        body="Refined body.",
    )
    assert updated is not None
    assert updated.name == "Updated template"
    assert updated.body == "Refined body."

    retired = await repo.retire_template(db_session, template.id)
    assert retired is not None
    assert retired.status == "retired"
    await db_session.commit()

    persisted = await repo.get_template(db_session, template.id)
    assert persisted is not None
    assert persisted.status == "retired"
    assert persisted.body == "Refined body."


@pytest.mark.asyncio
async def test_migration_0073_upgrade_downgrade_roundtrip() -> None:
    downgrade = _run_alembic("downgrade", "0072")
    assert downgrade.returncode == 0, downgrade.stderr

    try:
        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            assert "templates" not in tables
        finally:
            await engine.dispose()

        upgrade = _run_alembic("upgrade", "head")
        assert upgrade.returncode == 0, upgrade.stderr

        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
                columns = await conn.run_sync(
                    lambda sync_conn: {
                        column["name"] for column in inspect(sync_conn).get_columns("templates")
                    }
                )
            assert "templates" in tables
            assert {
                "id",
                "profile_id",
                "template_key",
                "name",
                "asset_type",
                "body",
                "status",
                "superseded_by",
                "created_at",
                "updated_at",
            }.issubset(columns)
        finally:
            await engine.dispose()

        downgrade_again = _run_alembic("downgrade", "0072")
        assert downgrade_again.returncode == 0, downgrade_again.stderr

        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                tables_after = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
            assert "templates" not in tables_after
        finally:
            await engine.dispose()
    finally:
        restore = _run_alembic("upgrade", "head")
        assert restore.returncode == 0, restore.stderr


@pytest.mark.asyncio
async def test_seed_corpus_import_populates_template_rows(db_session: AsyncSession) -> None:
    from artemis.writing_rules.seed_corpus import import_writing_seed_corpus

    result = await import_writing_seed_corpus(db_session)
    await db_session.commit()

    profile_id = result["profileId"]
    templates = await repo.list_templates(db_session, profile_id, status="active")
    template_a = next(template for template in templates if template.template_key == "A")

    assert result["templatesUpserted"] == 6
    assert len(templates) == 6
    assert template_a.name == "15-second opener (Suite)"
    assert template_a.body.startswith("Amira is a Learning Agent for Reading Growth.")
    assert template_a.status == "active"
