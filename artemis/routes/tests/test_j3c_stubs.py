"""Tests for J3c stub endpoints: sessions, notifications, stats.

The jira overview stub was replaced by the real J5 implementation.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_jira_overview_not_connected(client: AsyncClient) -> None:
    resp = await client.get("/api/jira/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["connected"] is False
    assert "savedConfig" in data


@pytest.mark.anyio
async def test_sessions_stub_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_notifications_history_stub_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/notifications/history")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.anyio
async def test_stats_analytics_stub(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/analytics")
    assert resp.status_code == 200
    data = resp.json()
    assert "overview" in data
    assert data["overview"]["sessions"] == 0


@pytest.mark.anyio
async def test_stats_providers_shape(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/providers")
    assert resp.status_code == 200
    items = resp.json()
    assert isinstance(items, list)
    for item in items:
        assert "provider_id" in item
        assert "name" in item
        assert "configured" in item
        assert "healthy" in item


@pytest.mark.anyio
async def test_stats_providers_contains_anthropic(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/providers")
    assert resp.status_code == 200
    items = resp.json()
    provider_ids = [item["provider_id"] for item in items]
    assert "anthropic" in provider_ids
