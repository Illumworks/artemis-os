"""Tests for P3 Google OAuth token encryption at rest.

Verifies:
1. Tokens stored via the ORM are ciphertext in the raw BYTEA column (not plaintext).
2. ORM reads (via EncryptedToken TypeDecorator) transparently decrypt to the original value.
3. The token refresh path re-encrypts correctly (assign credential.access_token = new_value
   → flush/commit → raw column is still ciphertext, ORM read returns new plaintext).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.google_docs.models  # noqa: F401 — register models on Base.metadata
import artemis.identity.models  # noqa: F401 — register users table
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.google_docs.models import GoogleCredential
from artemis.google_docs.repository import upsert_google_credential

pytestmark = pytest.mark.asyncio

# ── Test DB wiring ────────────────────────────────────────────────────────────

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_enc",
)
if "artemis_test" not in _DB_URL:
    raise RuntimeError(
        f"REFUSING TO LOAD {__name__}: db_url={_DB_URL!r} is not a safe test database."
    )

_ENGINE = create_async_engine(_DB_URL, echo=False, poolclass=NullPool)
attach_pgvector_codec(_ENGINE)
db_module.engine = _ENGINE
db_module.SessionLocal = async_sessionmaker(
    bind=_ENGINE,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE_SQL = text("TRUNCATE google_credentials, users RESTART IDENTITY CASCADE")

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _configure_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "cf_access_enabled", False)
    monkeypatch.setattr(settings, "token", None)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_ENGINE, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


async def _seed_user(session: AsyncSession) -> int:
    """Insert a minimal user row and return its id."""
    await session.execute(
        text(
            "INSERT INTO users (id, email, name) VALUES (1, 'test@example.com', 'Test User')"
            " ON CONFLICT (id) DO NOTHING"
        )
    )
    await session.flush()
    return 1


# ── Helpers ───────────────────────────────────────────────────────────────────

_EXPIRY = datetime.now(UTC) + timedelta(hours=1)
_ACCESS = "ya29.PLAINTEXTTOKEN"
_REFRESH = "1//PLAINTEXTREFRESH"


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_raw_column_is_ciphertext_not_plaintext(db_session: AsyncSession) -> None:
    """Tokens written via upsert_google_credential are stored as encrypted bytes.

    The raw BYTEA SELECT should NOT contain the plaintext token string,
    while the ORM accessor returns the decrypted value unchanged.
    """
    user_id = await _seed_user(db_session)

    cred = await upsert_google_credential(
        db_session,
        user_id=user_id,
        purpose="personal",
        access_token=_ACCESS,
        refresh_token=_REFRESH,
        expiry=_EXPIRY,
        scope="openid",
        connected_email="test@example.com",
    )
    await db_session.commit()

    # ORM read — should return the original plaintext.
    assert cred.access_token == _ACCESS
    assert cred.refresh_token == _REFRESH

    # Raw SQL read — the bytes stored must NOT be the plaintext string.
    row = await db_session.execute(
        text("SELECT access_token, refresh_token FROM google_credentials WHERE id = :id"),
        {"id": cred.id},
    )
    raw = row.one()
    raw_access: bytes = bytes(raw[0])
    raw_refresh: bytes = bytes(raw[1])

    # Must NOT be the plaintext (either as utf-8 bytes or a substring match).
    assert _ACCESS.encode() not in raw_access, (
        f"access_token is stored plaintext: {raw_access[:60]!r}"
    )
    assert _REFRESH.encode() not in raw_refresh, (
        f"refresh_token is stored plaintext: {raw_refresh[:60]!r}"
    )

    # Sanity: the bytes should be non-trivially long (Fernet adds overhead).
    assert len(raw_access) > len(_ACCESS) + 40, "ciphertext suspiciously short"


async def test_orm_read_decrypts_to_original_plaintext(db_session: AsyncSession) -> None:
    """A fresh ORM select (no identity-map cache) decrypts to original tokens."""
    user_id = await _seed_user(db_session)

    await upsert_google_credential(
        db_session,
        user_id=user_id,
        purpose="personal",
        access_token=_ACCESS,
        refresh_token=_REFRESH,
        expiry=_EXPIRY,
        scope="openid",
        connected_email="test@example.com",
    )
    await db_session.commit()

    # Expire the session identity-map so SQLAlchemy re-fetches from DB.
    db_session.expire_all()

    result = await db_session.execute(
        select(GoogleCredential).where(
            GoogleCredential.user_id == user_id,
            GoogleCredential.purpose == "personal",
        )
    )
    fetched = result.scalar_one()
    assert fetched.access_token == _ACCESS
    assert fetched.refresh_token == _REFRESH


async def test_refresh_path_re_encrypts(db_session: AsyncSession) -> None:
    """Simulates the token refresh path:

    credential.access_token = new_token  →  flush/commit  →
        raw column is still ciphertext, ORM read returns new plaintext.
    """
    user_id = await _seed_user(db_session)

    cred = await upsert_google_credential(
        db_session,
        user_id=user_id,
        purpose="personal",
        access_token=_ACCESS,
        refresh_token=_REFRESH,
        expiry=_EXPIRY,
        scope="openid",
        connected_email="test@example.com",
    )
    await db_session.commit()

    # --- Simulate the refresh path (same pattern as routes/google_docs.py line ~105) ---
    new_token = "ya29.REFRESHEDTOKEN"
    cred.access_token = new_token
    await db_session.flush()
    await db_session.commit()

    # Raw column: still ciphertext, not the new plaintext.
    row = await db_session.execute(
        text("SELECT access_token FROM google_credentials WHERE id = :id"),
        {"id": cred.id},
    )
    raw_access: bytes = bytes(row.scalar_one())
    assert new_token.encode() not in raw_access, (
        f"refreshed access_token is stored plaintext: {raw_access[:60]!r}"
    )

    # ORM read: decrypts to the new token.
    cred_id = cred.id  # capture before expiring the identity map
    db_session.expire_all()
    result = await db_session.execute(
        select(GoogleCredential).where(GoogleCredential.id == cred_id)
    )
    refetched = result.scalar_one()
    assert refetched.access_token == new_token
    # Refresh token unchanged.
    assert refetched.refresh_token == _REFRESH


async def test_nullable_refresh_token_stored_as_null(db_session: AsyncSession) -> None:
    """refresh_token=None stays NULL in the DB (no empty ciphertext)."""
    user_id = await _seed_user(db_session)

    cred = await upsert_google_credential(
        db_session,
        user_id=user_id,
        purpose="marketing",
        access_token=_ACCESS,
        refresh_token=None,
        expiry=_EXPIRY,
        scope=None,
        connected_email=None,
    )
    await db_session.commit()

    row = await db_session.execute(
        text("SELECT refresh_token FROM google_credentials WHERE id = :id"),
        {"id": cred.id},
    )
    assert row.scalar_one() is None
    assert cred.refresh_token is None
