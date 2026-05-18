"""Tests for GET /api/calendar/overview and GET /api/meetings/overview."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.crypto import encrypt_credentials
from artemis.integrations.gcal.types import Event, EventDateTime

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    summary: str,
    start_offset_minutes: int,
    now: datetime | None = None,
) -> Event:
    """Build a minimal GCal Event with a dateTime start."""
    base = now or datetime.now(UTC)
    start_dt = base + timedelta(minutes=start_offset_minutes)
    end_dt = start_dt + timedelta(minutes=30)
    return Event(
        id=f"evt-{summary.lower().replace(' ', '-')}",
        summary=summary,
        start=EventDateTime(dateTime=start_dt.isoformat()),
        end=EventDateTime(dateTime=end_dt.isoformat()),
    )


async def _insert_gcal_integration(session: AsyncSession) -> None:
    """Insert an active gcal integration row using encrypt_credentials."""
    creds = {
        "access_token": "tok",
        "refresh_token": "ref",
        "client_id": "cid",
        "client_secret": "sec",
    }
    encrypted = encrypt_credentials(creds)
    await repo.upsert_integration(
        session,
        provider="gcal",
        workspace_id="test@example.com",
        encrypted_credentials=encrypted,
        display_name="test@example.com",
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Calendar overview tests
# ---------------------------------------------------------------------------


async def test_calendar_overview_not_connected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """When no gcal integration row exists, return not_connected."""
    resp = await client.get("/api/calendar/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "not_connected", "provider": "gcal"}


async def test_calendar_overview_http_200_not_connected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """not_connected path still returns HTTP 200."""
    resp = await client.get("/api/calendar/overview")
    assert resp.status_code == 200


async def test_calendar_overview_connected_no_events(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Connected, but GCalClient.list_events returns [] → ready with 0 meetings."""
    await _insert_gcal_integration(db_session)

    with patch(
        "artemis.routes.calendar.GCalClient.list_events",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = await client.get("/api/calendar/overview")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["today"]["meetingsCount"] == 0
    assert data["nextEvent"] is None


async def test_calendar_overview_http_200_ready(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Connected + no API errors → HTTP 200."""
    await _insert_gcal_integration(db_session)

    with patch(
        "artemis.routes.calendar.GCalClient.list_events",
        new_callable=AsyncMock,
        return_value=[],
    ):
        resp = await client.get("/api/calendar/overview")

    assert resp.status_code == 200


async def test_calendar_overview_connected_with_events(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Connected with 2 future events → meetingsCount=2, nextEvent populated."""
    await _insert_gcal_integration(db_session)

    now = datetime.now(UTC)
    events = [
        _make_event("Team sync", start_offset_minutes=30, now=now),
        _make_event("1:1 with manager", start_offset_minutes=90, now=now),
    ]

    with patch(
        "artemis.routes.calendar.GCalClient.list_events",
        new_callable=AsyncMock,
        return_value=events,
    ):
        resp = await client.get("/api/calendar/overview")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["today"]["meetingsCount"] == 2
    # Next event is the first future one
    assert data["nextEvent"] is not None
    assert data["nextEvent"]["title"] == "Team sync"
    assert "AM" in data["nextEvent"]["startLabel"] or "PM" in data["nextEvent"]["startLabel"]


async def test_calendar_overview_gcal_error_degrades_gracefully(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """If GCalClient raises, return not_connected (not a 500)."""
    await _insert_gcal_integration(db_session)

    with patch(
        "artemis.routes.calendar.GCalClient.list_events",
        new_callable=AsyncMock,
        side_effect=Exception("network failure"),
    ):
        resp = await client.get("/api/calendar/overview")

    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "not_connected", "provider": "gcal"}


# ---------------------------------------------------------------------------
# Meetings overview tests
# ---------------------------------------------------------------------------


async def test_meetings_overview_always_not_connected(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /api/meetings/overview always returns not_connected/granola."""
    resp = await client.get("/api/meetings/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"status": "not_connected", "provider": "granola"}


async def test_meetings_overview_http_200(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """meetings overview always HTTP 200."""
    resp = await client.get("/api/meetings/overview")
    assert resp.status_code == 200


async def test_meetings_overview_no_db_dependency(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """meetings overview returns the correct shape regardless of DB state."""
    resp = await client.get("/api/meetings/overview")
    data = resp.json()
    assert "status" in data
    assert "provider" in data
    assert data["provider"] == "granola"
