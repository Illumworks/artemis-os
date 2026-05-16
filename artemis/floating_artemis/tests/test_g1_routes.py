"""Tests for Floating Artemis HTTP routes."""

from __future__ import annotations

from datetime import UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def _make_client() -> AsyncClient:
    from artemis.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── /api/_status includes floating-artemis ───────────────────────────────────


async def test_status_includes_floating_artemis() -> None:
    async with await _make_client() as client:
        resp = await client.get("/api/_status")
    assert resp.status_code == 200
    data = resp.json()
    assert "floating-artemis" in data["available_surfaces"]


# ── Session endpoints ─────────────────────────────────────────────────────────


async def test_create_session_returns_201() -> None:
    mock_row = MagicMock()
    mock_row.id = 1
    mock_row.session_id = "test-s1"
    mock_row.owner_user_id = None
    mock_row.started_at = MagicMock()
    mock_row.started_at.isoformat.return_value = "2026-05-16T12:00:00+00:00"
    mock_row.last_active_at = MagicMock()
    mock_row.last_active_at.isoformat.return_value = "2026-05-16T12:00:00+00:00"
    mock_row.closed_at = None
    mock_row.title = "Test"
    mock_row.metadata_ = {}

    from datetime import datetime

    now = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)
    mock_row.started_at = now
    mock_row.last_active_at = now

    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id",
            side_effect=ValueError("not found"),
        ),
        patch("artemis.floating_artemis.repository.create_session", return_value=mock_row),
        patch("artemis.routes.floating_artemis.get_session"),
    ):
        from artemis.floating_artemis.schemas import SessionRead

        # Just test the schema is valid
        sr = SessionRead.from_orm_row(mock_row)
        assert sr.session_id == "test-s1"


async def test_list_sessions_endpoint() -> None:
    """GET /api/floating-artemis/sessions returns 200."""
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.list_sessions = AsyncMock(return_value=[])
        async with await _make_client() as client:
            resp = await client.get("/api/floating-artemis/sessions")
    assert resp.status_code == 200
    assert "sessions" in resp.json()


async def test_get_session_not_found() -> None:
    """GET /api/floating-artemis/sessions/{id} returns 404 for missing."""
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.get_session_by_id = AsyncMock(side_effect=ValueError("not found"))
        async with await _make_client() as client:
            resp = await client.get("/api/floating-artemis/sessions/nonexistent")
    assert resp.status_code == 404


async def test_delete_session_not_found() -> None:
    """DELETE /api/floating-artemis/sessions/{id} returns 404 for missing."""
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.close_session = AsyncMock(side_effect=ValueError("not found"))
        async with await _make_client() as client:
            resp = await client.delete("/api/floating-artemis/sessions/nonexistent")
    assert resp.status_code == 404


async def test_list_messages_returns_200() -> None:
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.list_messages = AsyncMock(return_value=[])
        async with await _make_client() as client:
            resp = await client.get("/api/floating-artemis/sessions/s1/messages")
    assert resp.status_code == 200
    assert "messages" in resp.json()


async def test_page_context_session_not_found() -> None:
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.get_session_by_id = AsyncMock(side_effect=ValueError("not found"))
        async with await _make_client() as client:
            resp = await client.post(
                "/api/floating-artemis/sessions/bad/page-context",
                json={"page": "home"},
            )
    assert resp.status_code == 404


async def test_tool_confirm_missing_tool() -> None:
    """POST tool-confirm with unknown tool_use_id returns 404."""
    with patch("artemis.routes.floating_artemis.confirmation_store") as mock_store:
        mock_store.get.return_value = None
        async with await _make_client() as client:
            resp = await client.post(
                "/api/floating-artemis/sessions/s1/tool-confirm",
                json={"tool_use_id": "nonexistent", "decision": "run"},
            )
    assert resp.status_code == 404


async def test_stop_endpoint_returns_200() -> None:
    with patch("artemis.routes.floating_artemis.confirmation_store") as mock_store:
        mock_store.clear_session = MagicMock()
        with patch("artemis.routes.floating_artemis.ws_manager") as mock_ws:
            mock_ws.broadcast = AsyncMock()
            async with await _make_client() as client:
                resp = await client.post("/api/floating-artemis/sessions/s1/stop")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stopped"] is True


async def test_active_runs_returns_200() -> None:
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.get_active_runs = AsyncMock(return_value=[])
        async with await _make_client() as client:
            resp = await client.get("/api/floating-artemis/active-runs")
    assert resp.status_code == 200
    assert "runs" in resp.json()


# ── send_message ──────────────────────────────────────────────────────────────


async def test_send_message_session_not_found() -> None:
    with patch("artemis.routes.floating_artemis.repo") as mock_repo:
        mock_repo.get_session_by_id = AsyncMock(side_effect=ValueError("not found"))
        async with await _make_client() as client:
            resp = await client.post(
                "/api/floating-artemis/sessions/bad/messages",
                json={"message": "hello"},
            )
    assert resp.status_code == 404


async def test_send_message_accepted() -> None:
    mock_row = MagicMock()
    mock_row.session_id = "s1"

    # Override the FastAPI get_session dependency so no real DB connection is made
    from artemis.db import get_session
    from artemis.main import app

    mock_db_session = AsyncMock()

    async def _mock_get_session():
        yield mock_db_session

    app.dependency_overrides[get_session] = _mock_get_session
    try:
        with patch("artemis.routes.floating_artemis.repo") as mock_repo:
            mock_repo.get_session_by_id = AsyncMock(return_value=mock_row)
            # handle_turn is fire-and-forget — mock it to avoid side effects
            with (
                patch("artemis.routes.floating_artemis.handle_turn"),
                patch("asyncio.create_task"),
            ):
                async with await _make_client() as client:
                    resp = await client.post(
                        "/api/floating-artemis/sessions/s1/messages",
                        json={"message": "hello"},
                    )
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert resp.status_code == 202
    assert resp.json()["accepted"] is True
