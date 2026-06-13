"""Focused tests for P3 Google multi-account, calendar cache, and Gmail reads."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import artemis.db as db_module
import artemis.google_docs.models  # noqa: F401
import artemis.identity.models  # noqa: F401
import artemis.integrations.gcal.models  # noqa: F401
import artemis.integrations.models  # noqa: F401
from artemis.config import settings
from artemis.db import attach_pgvector_codec
from artemis.google_docs.models import GoogleCredential
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.gcal.models import GCalEventCache
from artemis.integrations.gcal.sync import sync_recent_gcal_events_cache
from artemis.integrations.gcal.types import Event, EventDateTime
from artemis.integrations.repository import upsert_integration

pytestmark = pytest.mark.asyncio

_DB_URL = os.environ.get(
    "ARTEMIS_TEST_DB_URL",
    "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_google_multi",
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
    "TRUNCATE gcal_events_cache, integrations, google_credentials, users RESTART IDENTITY CASCADE"
)


@pytest.fixture(autouse=True)
def _configure_google_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", "google-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "google-client-secret")
    monkeypatch.setattr(settings, "cf_access_enabled", False)
    monkeypatch.setattr(settings, "token", None)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSession(_ENGINE, expire_on_commit=False) as session:
        async with session.begin():
            await session.execute(_TRUNCATE_SQL)
        yield session


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _ensure_current_user(client: AsyncClient) -> None:
    response = await client.get("/api/me")
    assert response.status_code == 200


async def test_gmail_messages_route_reads_from_personal_google_credential(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await _ensure_current_user(client)
    db_session.add(
        GoogleCredential(
            user_id=1,
            purpose="personal",
            access_token="gmail-access",
            refresh_token="gmail-refresh",
            expiry=datetime.now(UTC) + timedelta(hours=1),
            scope="https://www.googleapis.com/auth/gmail.readonly openid https://www.googleapis.com/auth/userinfo.email",
            connected_email="jon.fila@amiralearning.com",
        )
    )
    await db_session.commit()

    with patch(
        "artemis.integrations.gmail.client.GmailClient.list_recent_messages",
        new=AsyncMock(
            return_value=[
                {
                    "id": "msg-1",
                    "threadId": "thread-1",
                    "subject": "Weekly check-in",
                    "from": "Teammate <teammate@amiralearning.com>",
                    "date": "Fri, 13 Jun 2026 09:00:00 -0400",
                    "snippet": "Can you review the launch plan?",
                    "internalDate": "1781365200000",
                }
            ]
        ),
    ):
        response = await client.get("/api/gmail/messages")

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["messages"][0]["subject"] == "Weekly check-in"


async def test_sync_recent_gcal_events_cache_upserts_recent_events(
    db_session: AsyncSession,
) -> None:
    await upsert_integration(
        db_session,
        provider="gcal",
        workspace_id="jon.fila@amiralearning.com",
        encrypted_credentials=encrypt_credentials(
            {
                "access_token": "calendar-access",
                "refresh_token": "calendar-refresh",
                "client_id": "google-client-id",
                "client_secret": "google-client-secret",
            }
        ),
        display_name="jon.fila@amiralearning.com",
    )
    await db_session.commit()

    event = Event(
        id="evt-123",
        summary="Leadership sync",
        description="Review campaign commitments",
        start=EventDateTime(dateTime=datetime.now(UTC).isoformat()),
        end=EventDateTime(dateTime=(datetime.now(UTC) + timedelta(hours=1)).isoformat()),
        attendees=[],
    )

    with patch(
        "artemis.integrations.gcal.client.GCalClient.list_events",
        new=AsyncMock(return_value=[event]),
    ):
        synced = await sync_recent_gcal_events_cache(db_session)
    await db_session.commit()

    assert synced == 1
    cached = (await db_session.execute(select(GCalEventCache))).scalar_one()
    assert cached.event_id == "evt-123"
    assert cached.summary == "Leadership sync"
