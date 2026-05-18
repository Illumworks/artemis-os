"""B6 — Parallel chat pane session allocation.

Tests for POST /api/parallel/sessions.

DB-hitting tests mock the FA repository layer (same pattern as
artemis/floating_artemis/tests/test_g1_routes.py) to avoid Postgres
teardown issues in the shared event-loop test environment.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


def _mock_fa_session(session_id: str) -> MagicMock:
    """Return a minimal MagicMock that satisfies SessionRead.from_orm_row."""
    from datetime import UTC, datetime

    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    row = MagicMock()
    row.id = 1
    row.session_id = session_id
    row.owner_user_id = None
    row.started_at = now
    row.last_active_at = now
    row.closed_at = None
    row.title = "Parallel Pane"
    row.metadata_ = {}
    return row


def _patched_client() -> AsyncClient:
    from artemis.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ── Schema/routing smoke: validation only (no DB) ────────────────────────────


async def test_parallel_sessions_count_too_low() -> None:
    """count=1 is rejected (min is 2)."""
    async with _patched_client() as client:
        response = await client.post(
            "/api/parallel/sessions",
            json={"count": 1},
        )
    assert response.status_code == 422


async def test_parallel_sessions_count_too_high() -> None:
    """count=5 is rejected (max is 4)."""
    async with _patched_client() as client:
        response = await client.post(
            "/api/parallel/sessions",
            json={"count": 5},
        )
    assert response.status_code == 422


# ── Mocked-DB tests ───────────────────────────────────────────────────────────


async def test_parallel_sessions_pair() -> None:
    """count=2 returns exactly 2 pane IDs with a shared run_id."""
    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            side_effect=ValueError("not found"),
        ),
        patch(
            "artemis.floating_artemis.repository.create_session",
            new_callable=AsyncMock,
            side_effect=lambda db, *, session_id, **kw: _mock_fa_session(session_id),
        ),
        patch("artemis.routes.parallel.get_session"),
    ):
        async with _patched_client() as client:
            response = await client.post(
                "/api/parallel/sessions",
                json={"count": 2},
            )
    assert response.status_code == 201
    data = response.json()
    assert "pane_ids" in data
    assert "run_id" in data
    assert len(data["pane_ids"]) == 2
    for pid in data["pane_ids"]:
        assert data["run_id"] in pid


async def test_parallel_sessions_quad() -> None:
    """count=4 returns 4 unique pane IDs."""
    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            side_effect=ValueError("not found"),
        ),
        patch(
            "artemis.floating_artemis.repository.create_session",
            new_callable=AsyncMock,
            side_effect=lambda db, *, session_id, **kw: _mock_fa_session(session_id),
        ),
        patch("artemis.routes.parallel.get_session"),
    ):
        async with _patched_client() as client:
            response = await client.post(
                "/api/parallel/sessions",
                json={"count": 4},
            )
    assert response.status_code == 201
    data = response.json()
    assert len(data["pane_ids"]) == 4
    assert len(set(data["pane_ids"])) == 4


async def test_parallel_sessions_default_count() -> None:
    """Omitting count defaults to 2."""
    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            side_effect=ValueError("not found"),
        ),
        patch(
            "artemis.floating_artemis.repository.create_session",
            new_callable=AsyncMock,
            side_effect=lambda db, *, session_id, **kw: _mock_fa_session(session_id),
        ),
        patch("artemis.routes.parallel.get_session"),
    ):
        async with _patched_client() as client:
            response = await client.post(
                "/api/parallel/sessions",
                json={},
            )
    assert response.status_code == 201
    assert len(response.json()["pane_ids"]) == 2


async def test_parallel_sessions_run_id_unique() -> None:
    """Two separate calls produce different run_ids."""
    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            side_effect=ValueError("not found"),
        ),
        patch(
            "artemis.floating_artemis.repository.create_session",
            new_callable=AsyncMock,
            side_effect=lambda db, *, session_id, **kw: _mock_fa_session(session_id),
        ),
        patch("artemis.routes.parallel.get_session"),
    ):
        async with _patched_client() as client:
            r1 = await client.post("/api/parallel/sessions", json={"count": 2})
            r2 = await client.post("/api/parallel/sessions", json={"count": 2})
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["run_id"] != r2.json()["run_id"]
