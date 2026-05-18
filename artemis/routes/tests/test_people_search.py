"""Tests for GET /api/people/search.

Covers:
- Merge + dedupe logic when both providers return results
- Source labelling (gcal / slack / both)
- Empty result when no integrations are connected
- Only-Slack path
- Only-GCal path (People API)
- In-memory cache hit (second request served from cache, no extra API calls)
- Name/avatar preference rules during merge
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.integrations import repository as repo
from artemis.integrations.crypto import encrypt_credentials

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gcal_creds() -> dict[str, object]:
    return {
        "access_token": "gcal-tok",
        "refresh_token": "gcal-ref",
        "client_id": "cid",
        "client_secret": "sec",
    }


def _slack_creds() -> dict[str, object]:
    return {"access_token": "xoxb-slack-tok"}


async def _insert_integration(
    session: AsyncSession,
    provider: str,
    creds: dict[str, object],
) -> None:
    encrypted = encrypt_credentials(creds)
    await repo.upsert_integration(
        session,
        provider=provider,
        workspace_id="ws-test",
        encrypted_credentials=encrypted,
    )
    await session.commit()


def _gcal_person(name: str, email: str, avatar: str | None = None) -> dict[str, Any]:
    return {"name": name, "email": email, "avatarUrl": avatar}


def _slack_member(
    real_name: str,
    email: str,
    display_name: str = "",
    avatar: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"U-{email}",
        "real_name": real_name,
        "is_bot": False,
        "deleted": False,
        "profile": {
            "display_name": display_name,
            "email": email,
            "image_48": avatar or "",
        },
    }


# ---------------------------------------------------------------------------
# Tests — merge + dedupe logic (unit-level, no DB needed)
# ---------------------------------------------------------------------------


def test_merge_disjoint() -> None:
    """Two non-overlapping sets → all entries preserved, source labels correct."""
    from artemis.routes.people import _merge

    gcal = [{"name": "Alice G", "email": "alice@example.com", "source": "gcal", "avatarUrl": None}]
    slack = [{"name": "Bob S", "email": "bob@example.com", "source": "slack", "avatarUrl": None}]

    result = _merge(gcal, slack)
    assert len(result) == 2
    by_email = {r["email"]: r for r in result}
    assert by_email["alice@example.com"]["source"] == "gcal"
    assert by_email["bob@example.com"]["source"] == "slack"


def test_merge_dedupe_same_email() -> None:
    """Same email from both providers → single entry with source='both'."""
    from artemis.routes.people import _merge

    gcal = [
        {
            "name": "Carol GCal",
            "email": "carol@example.com",
            "source": "gcal",
            "avatarUrl": "https://gcal-avatar",
        }
    ]
    slack = [
        {
            "name": "Carol S",
            "email": "carol@example.com",
            "source": "slack",
            "avatarUrl": "https://slack-avatar",
        }
    ]

    result = _merge(gcal, slack)
    assert len(result) == 1
    person = result[0]
    assert person["source"] == "both"
    assert person["email"] == "carol@example.com"
    # gcal avatar should be preferred (already present)
    assert person["avatarUrl"] == "https://gcal-avatar"


def test_merge_prefers_longer_name() -> None:
    """When slack name is longer, it wins."""
    from artemis.routes.people import _merge

    gcal = [{"name": "D", "email": "d@example.com", "source": "gcal", "avatarUrl": None}]
    slack = [
        {
            "name": "Diana Long Name",
            "email": "d@example.com",
            "source": "slack",
            "avatarUrl": "sl-avatar",
        }
    ]

    result = _merge(gcal, slack)
    assert result[0]["name"] == "Diana Long Name"
    # gcal avatar absent, so slack avatar fills in
    assert result[0]["avatarUrl"] == "sl-avatar"


def test_merge_case_insensitive_email() -> None:
    """Emails differing only in case are treated as the same person."""
    from artemis.routes.people import _merge

    gcal = [{"name": "Eve", "email": "Eve@EXAMPLE.COM", "source": "gcal", "avatarUrl": None}]
    slack = [{"name": "Eve S", "email": "eve@example.com", "source": "slack", "avatarUrl": None}]

    result = _merge(gcal, slack)
    assert len(result) == 1
    assert result[0]["source"] == "both"


# ---------------------------------------------------------------------------
# Tests — route-level with mocked providers
# ---------------------------------------------------------------------------


async def test_search_no_integrations(client: AsyncClient, db_session: AsyncSession) -> None:
    """No providers connected → 200 empty list."""
    resp = await client.get(
        "/api/people/search", params={"q": "alice"}, headers={"Authorization": "Bearer dev"}
    )
    assert resp.status_code == 200
    assert resp.json() == []


async def test_search_slack_only(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only Slack connected → results from Slack, gcal skipped."""
    await _insert_integration(db_session, "slack", _slack_creds())

    with patch(
        "artemis.routes.people._fetch_slack_people",
        new=AsyncMock(
            return_value=[
                {
                    "name": "Alice Walker",
                    "email": "alice@example.com",
                    "source": "slack",
                    "avatarUrl": None,
                },
            ]
        ),
    ):
        resp = await client.get(
            "/api/people/search",
            params={"q": "alice"},
            headers={"Authorization": "Bearer dev"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["email"] == "alice@example.com"
    assert data[0]["source"] == "slack"


async def test_search_gcal_only(client: AsyncClient, db_session: AsyncSession) -> None:
    """Only GCal connected → results from GCal, Slack skipped."""
    await _insert_integration(db_session, "gcal", _gcal_creds())

    with patch(
        "artemis.routes.people._fetch_gcal_people",
        new=AsyncMock(
            return_value=[
                {
                    "name": "Frank Google",
                    "email": "frank@example.com",
                    "source": "gcal",
                    "avatarUrl": "https://av",
                },
            ]
        ),
    ):
        resp = await client.get(
            "/api/people/search",
            params={"q": "frank"},
            headers={"Authorization": "Bearer dev"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    assert data[0]["source"] == "gcal"
    assert data[0]["avatarUrl"] == "https://av"


async def test_search_both_providers_merged(client: AsyncClient, db_session: AsyncSession) -> None:
    """Both providers connected → results merged and deduped on email."""
    await _insert_integration(db_session, "gcal", _gcal_creds())
    await _insert_integration(db_session, "slack", _slack_creds())

    gcal_people = [
        {
            "name": "Grace G",
            "email": "grace@example.com",
            "source": "gcal",
            "avatarUrl": "https://gcal-av",
        },
    ]
    slack_people = [
        {"name": "Grace Slack", "email": "grace@example.com", "source": "slack", "avatarUrl": None},
        {"name": "Henry H", "email": "henry@example.com", "source": "slack", "avatarUrl": None},
    ]

    with (
        patch("artemis.routes.people._fetch_gcal_people", new=AsyncMock(return_value=gcal_people)),
        patch(
            "artemis.routes.people._fetch_slack_people", new=AsyncMock(return_value=slack_people)
        ),
    ):
        resp = await client.get(
            "/api/people/search",
            params={"q": "gr"},
            headers={"Authorization": "Bearer dev"},
        )

    assert resp.status_code == 200
    data = resp.json()
    by_email = {p["email"]: p for p in data}

    # grace should appear once, source='both'
    assert "grace@example.com" in by_email
    assert by_email["grace@example.com"]["source"] == "both"
    # gcal avatar preserved
    assert by_email["grace@example.com"]["avatarUrl"] == "https://gcal-av"

    # henry only from slack
    assert "henry@example.com" in by_email
    assert by_email["henry@example.com"]["source"] == "slack"


async def test_search_empty_query_returns_empty(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Empty query string → 200 empty list, no provider calls made."""
    await _insert_integration(db_session, "slack", _slack_creds())

    with patch("artemis.routes.people._fetch_slack_people", new=AsyncMock()) as mock_slack:
        resp = await client.get(
            "/api/people/search",
            params={"q": ""},
            headers={"Authorization": "Bearer dev"},
        )
        mock_slack.assert_not_called()

    assert resp.status_code == 200
    assert resp.json() == []


async def test_search_cache_hit(client: AsyncClient, db_session: AsyncSession) -> None:
    """Second identical request served from in-memory cache."""
    await _insert_integration(db_session, "slack", _slack_creds())

    mock_result = [
        {"name": "Ivy I", "email": "ivy@example.com", "source": "slack", "avatarUrl": None}
    ]

    # Clear cache to ensure a cold start
    import artemis.routes.people as people_mod

    people_mod._cache.clear()

    call_count = 0

    async def _mock_fetch_slack(token: str, query: str, limit: int) -> list[dict[str, Any]]:
        nonlocal call_count
        call_count += 1
        return mock_result

    with (
        patch("artemis.routes.people._fetch_gcal_people", new=AsyncMock(return_value=[])),
        patch("artemis.routes.people._fetch_slack_people", new=_mock_fetch_slack),
    ):
        r1 = await client.get(
            "/api/people/search",
            params={"q": "ivy"},
            headers={"Authorization": "Bearer dev"},
        )
        r2 = await client.get(
            "/api/people/search",
            params={"q": "ivy"},
            headers={"Authorization": "Bearer dev"},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
    # Provider should have been called only once; second request served from cache
    assert call_count == 1
