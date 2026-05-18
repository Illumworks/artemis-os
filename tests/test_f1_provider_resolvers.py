"""Phase F1 — LLM provider credential resolver tests.

Tests:
  1.  test_resolve_anthropic_db_wins         — DB api_key overrides ANTHROPIC_API_KEY env
  2.  test_resolve_anthropic_env_fallback    — no DB row → env var used
  3.  test_resolve_anthropic_missing_raises  — neither DB nor env → MissingProviderConfigError
  4.  test_resolve_openai_db_wins            — DB api_key overrides OPENAI_API_KEY env
  5.  test_resolve_openai_env_fallback       — no DB row → env var used
  6.  test_resolve_openai_missing_raises     — neither DB nor env → MissingProviderConfigError
  7.  test_resolve_gemini_db_wins            — DB api_key overrides GEMINI_API_KEY env
  8.  test_resolve_gemini_env_fallback       — no DB row → env var used
  9.  test_resolve_gemini_missing_raises     — neither DB nor env → MissingProviderConfigError
  10. test_anthropic_in_known_providers      — provider accepted by integrations API (GET 200)
  11. test_openai_in_known_providers         — provider accepted by integrations API (GET 200)
  12. test_gemini_in_known_providers         — provider accepted by integrations API (GET 200)
  13. test_anthropic_post_and_get            — POST api_key → configured_keys["api_key"] True
  14. test_openai_post_and_get              — POST api_key → configured_keys["api_key"] True
  15. test_gemini_post_and_get              — POST api_key → configured_keys["api_key"] True
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db as db_module
import artemis.integrations.models  # noqa: F401 — register ORM models
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB ───────────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text("TRUNCATE integration_configs RESTART IDENTITY CASCADE")


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
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── Anthropic resolver ────────────────────────────────────────────────────────


async def test_resolve_anthropic_db_wins(db_session: AsyncSession) -> None:
    """DB api_key beats ANTHROPIC_API_KEY env var."""
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_anthropic_config

    await repo.upsert_provider_config(db_session, "anthropic", {"api_key": "db-anthropic-key"})
    await db_session.commit()

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-anthropic-key"}):
        cfg = await resolve_anthropic_config(db_session)

    assert cfg.api_key == "db-anthropic-key"


async def test_resolve_anthropic_env_fallback(db_session: AsyncSession) -> None:
    """No DB row → env var fills the key."""
    from artemis.integrations.config_resolver import resolve_anthropic_config

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "env-key-fallback"}):
        cfg = await resolve_anthropic_config(db_session)

    assert cfg.api_key == "env-key-fallback"


async def test_resolve_anthropic_missing_raises(db_session: AsyncSession) -> None:
    """Neither DB nor env → MissingProviderConfigError."""
    from artemis.integrations.config_resolver import (
        MissingProviderConfigError,
        resolve_anthropic_config,
    )

    env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    with (
        patch.dict(os.environ, env_without_key, clear=True),
        pytest.raises(MissingProviderConfigError) as exc,
    ):
        await resolve_anthropic_config(db_session)

    assert exc.value.provider == "anthropic"
    assert "api_key" in exc.value.missing_fields


# ── OpenAI resolver ───────────────────────────────────────────────────────────


async def test_resolve_openai_db_wins(db_session: AsyncSession) -> None:
    """DB api_key beats OPENAI_API_KEY env var."""
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_openai_config

    await repo.upsert_provider_config(db_session, "openai", {"api_key": "db-openai-key"})
    await db_session.commit()

    with patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai-key"}):
        cfg = await resolve_openai_config(db_session)

    assert cfg.api_key == "db-openai-key"


async def test_resolve_openai_env_fallback(db_session: AsyncSession) -> None:
    """No DB row → env var fills the key."""
    from artemis.integrations.config_resolver import resolve_openai_config

    with patch.dict(os.environ, {"OPENAI_API_KEY": "env-openai-fallback"}):
        cfg = await resolve_openai_config(db_session)

    assert cfg.api_key == "env-openai-fallback"


async def test_resolve_openai_missing_raises(db_session: AsyncSession) -> None:
    """Neither DB nor env → MissingProviderConfigError."""
    from artemis.integrations.config_resolver import (
        MissingProviderConfigError,
        resolve_openai_config,
    )

    env_without_key = {k: v for k, v in os.environ.items() if k != "OPENAI_API_KEY"}
    with (
        patch.dict(os.environ, env_without_key, clear=True),
        pytest.raises(MissingProviderConfigError) as exc,
    ):
        await resolve_openai_config(db_session)

    assert exc.value.provider == "openai"
    assert "api_key" in exc.value.missing_fields


# ── Gemini resolver ───────────────────────────────────────────────────────────


async def test_resolve_gemini_db_wins(db_session: AsyncSession) -> None:
    """DB api_key beats GEMINI_API_KEY env var."""
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_gemini_config

    await repo.upsert_provider_config(db_session, "gemini", {"api_key": "db-gemini-key"})
    await db_session.commit()

    with patch.dict(os.environ, {"GEMINI_API_KEY": "env-gemini-key"}):
        cfg = await resolve_gemini_config(db_session)

    assert cfg.api_key == "db-gemini-key"


async def test_resolve_gemini_env_fallback(db_session: AsyncSession) -> None:
    """No DB row → env var fills the key."""
    from artemis.integrations.config_resolver import resolve_gemini_config

    with patch.dict(os.environ, {"GEMINI_API_KEY": "env-gemini-fallback"}):
        cfg = await resolve_gemini_config(db_session)

    assert cfg.api_key == "env-gemini-fallback"


async def test_resolve_gemini_missing_raises(db_session: AsyncSession) -> None:
    """Neither DB nor env → MissingProviderConfigError."""
    from artemis.integrations.config_resolver import (
        MissingProviderConfigError,
        resolve_gemini_config,
    )

    env_without_key = {k: v for k, v in os.environ.items() if k != "GEMINI_API_KEY"}
    with (
        patch.dict(os.environ, env_without_key, clear=True),
        pytest.raises(MissingProviderConfigError) as exc,
    ):
        await resolve_gemini_config(db_session)

    assert exc.value.provider == "gemini"
    assert "api_key" in exc.value.missing_fields


# ── API-level: new providers accepted by integrations route ───────────────────


async def test_anthropic_in_known_providers(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /providers/anthropic/config should return 200, not 404."""
    r = await client.get("/api/integrations/providers/anthropic/config")
    assert r.status_code == 200
    body = r.json()
    assert "ever_configured" in body


async def test_openai_in_known_providers(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /providers/openai/config should return 200, not 404."""
    r = await client.get("/api/integrations/providers/openai/config")
    assert r.status_code == 200
    body = r.json()
    assert "ever_configured" in body


async def test_gemini_in_known_providers(client: AsyncClient, db_session: AsyncSession) -> None:
    """GET /providers/gemini/config should return 200, not 404."""
    r = await client.get("/api/integrations/providers/gemini/config")
    assert r.status_code == 200
    body = r.json()
    assert "ever_configured" in body


# ── POST + GET round-trip for each LLM provider ──────────────────────────────


async def test_anthropic_post_and_get(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST api_key for anthropic → GET shows configured_keys['api_key'] = True."""
    r = await client.post(
        "/api/integrations/providers/anthropic/config",
        json={"api_key": "sk-ant-test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ever_configured"] is True
    assert body["configured_keys"].get("api_key") is True


async def test_openai_post_and_get(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST api_key for openai → GET shows configured_keys['api_key'] = True."""
    r = await client.post(
        "/api/integrations/providers/openai/config",
        json={"api_key": "sk-openai-test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ever_configured"] is True
    assert body["configured_keys"].get("api_key") is True


async def test_gemini_post_and_get(client: AsyncClient, db_session: AsyncSession) -> None:
    """POST api_key for gemini → GET shows configured_keys['api_key'] = True."""
    r = await client.post(
        "/api/integrations/providers/gemini/config",
        json={"api_key": "AIza-test-key"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ever_configured"] is True
    assert body["configured_keys"].get("api_key") is True
