"""Tests for the four previously-404 stub endpoints.

Verifies each returns 200 with the correct JSON shape so the browser
console stays clean of these specific 404 errors.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_version_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    assert isinstance(data["version"], str)
    assert len(data["version"]) > 0


@pytest.mark.asyncio
async def test_notifications_unread_count_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/api/notifications/unread-count")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"count": 0}


@pytest.mark.asyncio
async def test_stats_alerts_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/alerts")
    assert resp.status_code == 200
    data = resp.json()
    assert "alerts" in data
    assert "count" in data
    assert isinstance(data["alerts"], list)
    assert data["count"] == 0


@pytest.mark.asyncio
async def test_memory_embeddings_status_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/api/memory/embeddings/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["queued"] == 0
    assert data["processing"] == 0
    assert data["completed_today"] == 0
    assert data["last_error"] is None


@pytest.mark.asyncio
async def test_stats_agent_metrics_not_regressed(client: AsyncClient) -> None:
    resp = await client.get("/api/stats/agent-metrics")
    assert resp.status_code == 200
    assert "overview" in resp.json()


@pytest.mark.asyncio
async def test_memory_conflicts_not_regressed(client: AsyncClient) -> None:
    resp = await client.get("/api/memory/conflicts")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
