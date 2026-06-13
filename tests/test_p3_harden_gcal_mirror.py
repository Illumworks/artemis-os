"""Regression tests — harden personal→gcal integration mirror (no false revoke).

Covers the four cases from briefs/p3-harden-gcal-mirror.md:

  (a) double consent with calendar scope  → integration stays/ends active
  (b) re-consent heals a revoked row      → flips back to active
  (c) connect without calendar scope      → existing active row NOT revoked
  (d) explicit disconnect                 → row revoked (guard regression)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.google_docs.models  # noqa: F401
import artemis.identity.models  # noqa: F401
import artemis.integrations.models  # noqa: F401
from artemis.db import attach_pgvector_codec
from artemis.google_docs.models import GoogleCredential
from artemis.google_integration import (
    revoke_personal_google_integrations,
    sync_personal_google_integrations,
)
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.models import Integration
from artemis.integrations.repository import upsert_integration

pytestmark = pytest.mark.asyncio

# ── Test DB setup ─────────────────────────────────────────────────────────────

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_harden",
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

_TRUNCATE_SQL = text(
    "TRUNCATE integrations, google_credentials RESTART IDENTITY CASCADE"
)

_EMAIL = "jon.fila@amiralearning.com"
_CALENDAR_SCOPE = (
    "https://www.googleapis.com/auth/calendar "
    "https://www.googleapis.com/auth/calendar.events "
    "openid https://www.googleapis.com/auth/userinfo.email"
)
_NO_CALENDAR_SCOPE = "openid https://www.googleapis.com/auth/userinfo.email"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_ENGINE, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


def _make_credential(scope: str, email: str = _EMAIL) -> GoogleCredential:
    """Build an unsaved GoogleCredential with the given scope string."""
    return GoogleCredential(
        user_id=1,
        purpose="personal",
        access_token="access-tok",
        refresh_token="refresh-tok",
        expiry=datetime.now(UTC) + timedelta(hours=1),
        scope=scope,
        connected_email=email,
    )


async def _get_integration(session: AsyncSession, email: str = _EMAIL) -> Integration | None:
    result = await session.execute(
        select(Integration).where(
            Integration.provider == "gcal",
            Integration.workspace_id == email,
            Integration.agent_id == "default",
        )
    )
    return result.scalar_one_or_none()


# ── Tests ─────────────────────────────────────────────────────────────────────


async def test_double_consent_with_calendar_scope_stays_active(
    db_session: AsyncSession,
) -> None:
    """(a) Two personal consents both with calendar scope → ends active with scopes."""
    cred = _make_credential(_CALENDAR_SCOPE)

    # First consent
    await sync_personal_google_integrations(
        db_session,
        credential=cred,
        client_id="client-id",
        client_secret="client-secret",
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None, "integration row must exist after first consent"
    assert row.status == "active"

    # Second consent (re-consent)
    await sync_personal_google_integrations(
        db_session,
        credential=cred,
        client_id="client-id",
        client_secret="client-secret",
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None
    assert row.status == "active", f"expected active after double consent, got {row.status!r}"
    assert row.scopes, "scopes must be non-empty after double consent"
    assert "https://www.googleapis.com/auth/calendar" in row.scopes


async def test_reconsent_heals_revoked_row(
    db_session: AsyncSession,
) -> None:
    """(b) Start with a revoked gcal row → re-consent with calendar scope → active."""
    # Seed a revoked row directly
    await upsert_integration(
        db_session,
        provider="gcal",
        workspace_id=_EMAIL,
        encrypted_credentials=encrypt_credentials({"access_token": "old-tok"}),
        display_name=_EMAIL,
        scopes=[],
    )
    await db_session.commit()

    # Manually flip it to revoked (simulating the bug scenario)
    from sqlalchemy import update

    await db_session.execute(
        update(Integration)
        .where(
            Integration.provider == "gcal",
            Integration.workspace_id == _EMAIL,
            Integration.agent_id == "default",
        )
        .values(status="revoked", scopes=None)
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None and row.status == "revoked", "precondition: row must be revoked"

    # Re-consent with calendar scope must heal the row
    cred = _make_credential(_CALENDAR_SCOPE)
    await sync_personal_google_integrations(
        db_session,
        credential=cred,
        client_id="client-id",
        client_secret="client-secret",
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None
    assert row.status == "active", (
        f"expected row to flip to active after re-consent, got {row.status!r}"
    )
    assert row.scopes, "scopes must be populated after healing"
    assert "https://www.googleapis.com/auth/calendar" in row.scopes


async def test_connect_without_calendar_scope_does_not_revoke_active_row(
    db_session: AsyncSession,
) -> None:
    """(c) Existing active integration + consent lacking calendar → stays active."""
    # Seed an active row
    await upsert_integration(
        db_session,
        provider="gcal",
        workspace_id=_EMAIL,
        encrypted_credentials=encrypt_credentials({"access_token": "good-tok"}),
        display_name=_EMAIL,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None and row.status == "active", "precondition: row must be active"

    # Consent whose scope omits calendar → must do nothing
    cred_no_cal = _make_credential(_NO_CALENDAR_SCOPE)
    await sync_personal_google_integrations(
        db_session,
        credential=cred_no_cal,
        client_id="client-id",
        client_secret="client-secret",
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None
    assert row.status == "active", (
        f"expected row to stay active when consent lacks calendar scope, got {row.status!r}"
    )


async def test_explicit_disconnect_still_revokes(
    db_session: AsyncSession,
) -> None:
    """(d) Explicit disconnect path → integration revoked (guard against regression)."""
    # Seed an active row
    await upsert_integration(
        db_session,
        provider="gcal",
        workspace_id=_EMAIL,
        encrypted_credentials=encrypt_credentials({"access_token": "tok"}),
        display_name=_EMAIL,
    )
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None and row.status == "active", "precondition: row must be active"

    # Explicit disconnect
    await revoke_personal_google_integrations(db_session, connected_email=_EMAIL)
    await db_session.commit()

    row = await _get_integration(db_session)
    assert row is not None
    assert row.status == "revoked", (
        f"expected disconnect to revoke the integration, got {row.status!r}"
    )
