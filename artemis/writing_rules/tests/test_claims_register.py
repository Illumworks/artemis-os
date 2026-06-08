"""Claims Register parser, repository, seed, and migration tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import NullPool, inspect
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from artemis.writing_rules import repository as repo
from artemis.writing_rules.seed_corpus import import_claims_register, parse_claims_register_markdown

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)

_SAMPLE_CLAIMS_MARKDOWN = """
## Claim 099 — Category One
Tier: 1
Approved phrasing:
"First approved claim."
Packaging: None
Notes: Use as the opener.

## Claim 100 — High Stakes
Tier: 4
Approved phrasing (verbatim):
"Second approved claim."
Required packaging (mandatory):
- Include the proof pack.
- Include the conditions where results were observed.
Notes:
- Never paraphrase.
- Retain the source citation.
"""


async def _create_profile(session: AsyncSession, name: str = "Claims Profile") -> int:
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


def test_parse_claims_register_markdown_fixture() -> None:
    claims = parse_claims_register_markdown(_SAMPLE_CLAIMS_MARKDOWN)

    assert len(claims) == 2
    assert claims[0].claim_code == "099"
    assert claims[0].category == "Category One"
    assert claims[0].tier == 1
    assert claims[0].approved_phrasing == "First approved claim."
    assert claims[0].packaging is None
    assert claims[0].notes == "Use as the opener."

    assert claims[1].claim_code == "100"
    assert claims[1].tier == 4
    assert claims[1].approved_phrasing == "Second approved claim."
    assert claims[1].packaging == (
        "- Include the proof pack.\n- Include the conditions where results were observed."
    )
    assert claims[1].notes == "- Never paraphrase.\n- Retain the source citation."


@pytest.mark.asyncio
async def test_import_claims_register_is_idempotent(db_session: AsyncSession) -> None:
    profile_id = await _create_profile(db_session)

    imported_first = await import_claims_register(
        db_session, profile_id, markdown=_SAMPLE_CLAIMS_MARKDOWN
    )
    await db_session.commit()

    imported_second = await import_claims_register(
        db_session, profile_id, markdown=_SAMPLE_CLAIMS_MARKDOWN
    )
    await db_session.commit()

    claims = await repo.list_claims(db_session, profile_id, status="approved")
    assert imported_first == 2
    assert imported_second == 2
    assert len(claims) == 2
    assert [claim.claim_code for claim in claims] == ["099", "100"]


@pytest.mark.asyncio
async def test_claim_repository_status_lifecycle_is_lossless(db_session: AsyncSession) -> None:
    profile_id = await _create_profile(db_session, name="Lifecycle Profile")

    claim = await repo.create_claim(
        db_session,
        profile_id=profile_id,
        claim_code="201",
        category="Lifecycle",
        tier=2,
        approved_phrasing="Proposed claim phrasing.",
        notes="Needs review.",
    )
    await db_session.commit()
    assert claim.status == "proposed"

    approved = await repo.approve_claim(db_session, claim.id)
    assert approved is not None
    assert approved.status == "approved"

    updated = await repo.update_claim(db_session, claim.id, notes="Approved and edited.")
    assert updated is not None
    assert updated.notes == "Approved and edited."

    retired = await repo.retire_claim(db_session, claim.id)
    assert retired is not None
    assert retired.status == "retired"
    await db_session.commit()

    persisted = await repo.get_claim(db_session, claim.id)
    assert persisted is not None
    assert persisted.status == "retired"
    assert persisted.notes == "Approved and edited."

    with pytest.raises(ValueError, match="cannot approve"):
        await repo.approve_claim(db_session, claim.id)


@pytest.mark.asyncio
async def test_migration_0072_upgrade_downgrade_roundtrip() -> None:
    downgrade = _run_alembic("downgrade", "0071")
    assert downgrade.returncode == 0, downgrade.stderr

    try:
        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                tables = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            assert "claims" not in tables
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
                        column["name"] for column in inspect(sync_conn).get_columns("claims")
                    }
                )
            assert "claims" in tables
            assert {
                "id",
                "profile_id",
                "claim_code",
                "category",
                "tier",
                "approved_phrasing",
                "packaging",
                "notes",
                "source",
                "status",
                "superseded_by",
                "created_at",
                "updated_at",
            }.issubset(columns)
        finally:
            await engine.dispose()

        downgrade_again = _run_alembic("downgrade", "0071")
        assert downgrade_again.returncode == 0, downgrade_again.stderr

        engine = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                tables_after = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
            assert "claims" not in tables_after
        finally:
            await engine.dispose()
    finally:
        restore = _run_alembic("upgrade", "head")
        assert restore.returncode == 0, restore.stderr


@pytest.mark.asyncio
async def test_seed_corpus_import_populates_claim_rows(db_session: AsyncSession) -> None:
    from artemis.writing_rules.seed_corpus import import_writing_seed_corpus

    result = await import_writing_seed_corpus(db_session)
    await db_session.commit()

    profile_id = result["profileId"]
    claims = await repo.list_claims(db_session, profile_id, status="approved")
    claim_001 = next(claim for claim in claims if claim.claim_code == "001")

    assert result["claimsUpserted"] == 8
    assert len(claims) == 8
    assert claim_001.category == "Identity / Category"
    assert claim_001.tier == 1
    assert claim_001.approved_phrasing == "Amira is the Learning Agent for Reading Growth."
    assert claim_001.notes == "Use early in every deck."
    assert claim_001.status == "approved"
