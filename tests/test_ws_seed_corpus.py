"""Tests for Writing Studio seed corpus import.

Covers:
  (a) import_writing_seed_corpus() seeds the expected profile, sources,
      rules, and examples into a clean DB.
  (b) A second call is fully idempotent — inserts zero duplicates.
  (c) POST /api/writing-studio/seed/import returns sensible counts in
      the shape the frontend importWritingSeedApi expects.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
from artemis.db import attach_pgvector_codec

# ---------------------------------------------------------------------------
# Test-DB bootstrap (same pattern as other integration tests in this suite)
# ---------------------------------------------------------------------------

_db_url = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test",
)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    """
    TRUNCATE
        templates,
        claims,
        writing_sources,
        writing_examples,
        writing_rules,
        writing_profiles
    RESTART IDENTITY CASCADE
    """
)

# Expected counts from the seed corpus SEED_MAP
# Files → targets:
#   00_MASTER_PROMPT   → profile_prompt  (not a rule or example)
#   01_MESSAGE_COMPASS → example
#   02_PRODUCT_CARDS   → example
#   03_AUDIENCE_ROUTER → example
#   04_GLOSSARY        → example
#   05_CLAIMS_REGISTER → example
#   06_PROOF_PACK_INDEX→ example
#   07_TEMPLATES       → example (type=template)
#   08_CHANGELOG       → source_only   (not a rule or example)
EXPECTED_SOURCES = 9  # one writing_sources row per file
EXPECTED_EXAMPLES = 7  # 01..07 minus 08 (source_only) and 00 (profile_prompt)
EXPECTED_RULES = 0  # no rule-target entries in the current corpus
EXPECTED_CLAIMS = 8  # 05_CLAIMS_REGISTER parses into 8 approved claim rows
EXPECTED_TEMPLATES = 6  # 07_TEMPLATES parses into 6 structured template rows


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def http_client() -> AsyncIterator[AsyncClient]:
    """HTTP client wired to the FastAPI app with a clean DB state."""
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    db_module.engine = engine
    db_module.SessionLocal = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with AsyncSession(engine, expire_on_commit=False) as setup_session, setup_session.begin():
        await setup_session.execute(_TRUNCATE)

    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Artemis-Token": os.environ.get("ARTEMIS_API_TOKEN", "test-token")},
    ) as ac:
        yield ac
    await engine.dispose()


# ---------------------------------------------------------------------------
# Unit-level tests (call the importer function directly)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_inserts_correct_counts(db_session: AsyncSession) -> None:
    """First import creates the expected number of profiles/sources/examples/rules."""
    from artemis.writing_rules.seed_corpus import import_writing_seed_corpus

    result = await import_writing_seed_corpus(db_session)
    await db_session.commit()

    assert result["profilesInserted"] == 1, "Should create exactly one profile"
    assert result["sourcesUpserted"] == EXPECTED_SOURCES, (
        f"Expected {EXPECTED_SOURCES} sources, got {result['sourcesUpserted']}"
    )
    assert result["examplesUpserted"] == EXPECTED_EXAMPLES, (
        f"Expected {EXPECTED_EXAMPLES} examples, got {result['examplesUpserted']}"
    )
    assert result["rulesUpserted"] == EXPECTED_RULES, (
        f"Expected {EXPECTED_RULES} rules, got {result['rulesUpserted']}"
    )
    assert result["claimsUpserted"] == EXPECTED_CLAIMS, (
        f"Expected {EXPECTED_CLAIMS} claims, got {result['claimsUpserted']}"
    )
    assert result["templatesUpserted"] == EXPECTED_TEMPLATES, (
        f"Expected {EXPECTED_TEMPLATES} templates, got {result['templatesUpserted']}"
    )
    assert result["profilePromptUpdated"] is True, "Master prompt should update profile"
    assert result["profileId"] is not None
    assert result["profileName"] == "Amira Marketing"
    assert len(result["imported"]) == EXPECTED_SOURCES


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    """Second import does not create duplicate rows."""
    from artemis.writing_rules import repository as repo
    from artemis.writing_rules.seed_corpus import import_writing_seed_corpus

    # First run
    await import_writing_seed_corpus(db_session)
    await db_session.commit()

    # Second run — counts must reflect upserted (not fresh inserted) but
    # the DB row counts must not double.
    await import_writing_seed_corpus(db_session)
    await db_session.commit()

    profiles = await repo.list_profiles(db_session)
    assert len(profiles) == 1, "Should still have exactly one profile after second run"

    sources = await repo.list_sources(db_session)
    assert len(sources) == EXPECTED_SOURCES, (
        f"Expected {EXPECTED_SOURCES} sources after 2nd run, got {len(sources)}"
    )

    examples = await repo.list_examples(db_session)
    assert len(examples) == EXPECTED_EXAMPLES, (
        f"Expected {EXPECTED_EXAMPLES} examples after 2nd run, got {len(examples)}"
    )

    rules = await repo.list_rules(db_session)
    assert len(rules) == EXPECTED_RULES, (
        f"Expected {EXPECTED_RULES} rules after 2nd run, got {len(rules)}"
    )

    claims = await repo.list_claims(db_session, profiles[0].id)
    assert len(claims) == EXPECTED_CLAIMS, (
        f"Expected {EXPECTED_CLAIMS} claims after 2nd run, got {len(claims)}"
    )

    templates = await repo.list_templates(db_session, profiles[0].id)
    assert len(templates) == EXPECTED_TEMPLATES, (
        f"Expected {EXPECTED_TEMPLATES} templates after 2nd run, got {len(templates)}"
    )


@pytest.mark.asyncio
async def test_seed_profile_has_system_prompt(db_session: AsyncSession) -> None:
    """The active profile's system_prompt is set from 00_MASTER_PROMPT.md."""
    from artemis.writing_rules import repository as repo
    from artemis.writing_rules.seed_corpus import import_writing_seed_corpus

    await import_writing_seed_corpus(db_session)
    await db_session.commit()

    profile = await repo.get_active_profile(db_session)
    assert profile is not None
    assert profile.system_prompt is not None
    # Normalization strips backslash escapes → the prompt starts with bare #
    assert profile.system_prompt.startswith("# AMIRA MARKETING GPT INSTRUCTIONS"), (
        f"Unexpected system_prompt start: {profile.system_prompt[:60]!r}"
    )


# ---------------------------------------------------------------------------
# HTTP endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_import_endpoint_returns_counts(http_client: AsyncClient) -> None:
    """POST /api/writing-studio/seed/import returns expected count fields."""
    response = await http_client.post("/api/writing-studio/seed/import")
    assert response.status_code == 200, response.text
    data = response.json()

    # Fields the frontend reads
    assert "sourcesUpserted" in data
    assert "rulesUpserted" in data
    assert "claimsUpserted" in data
    assert "examplesUpserted" in data
    assert "templatesUpserted" in data
    assert data["sourcesUpserted"] == EXPECTED_SOURCES
    assert data["examplesUpserted"] == EXPECTED_EXAMPLES
    assert data["rulesUpserted"] == EXPECTED_RULES
    assert data["claimsUpserted"] == EXPECTED_CLAIMS
    assert data["templatesUpserted"] == EXPECTED_TEMPLATES
    assert data["profilePromptUpdated"] is True


@pytest.mark.asyncio
async def test_seed_import_endpoint_idempotent(http_client: AsyncClient) -> None:
    """Two calls to POST /api/writing-studio/seed/import give consistent results."""
    r1 = await http_client.post("/api/writing-studio/seed/import")
    assert r1.status_code == 200
    r2 = await http_client.post("/api/writing-studio/seed/import")
    assert r2.status_code == 200
    d1 = r1.json()
    d2 = r2.json()
    # Counts should be the same on both runs (upsert semantics)
    assert d1["sourcesUpserted"] == d2["sourcesUpserted"]
    assert d1["examplesUpserted"] == d2["examplesUpserted"]
    assert d1["rulesUpserted"] == d2["rulesUpserted"]
    assert d1["claimsUpserted"] == d2["claimsUpserted"]
    assert d1["templatesUpserted"] == d2["templatesUpserted"]
