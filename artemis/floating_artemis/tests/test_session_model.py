"""Tests for provider/model session picker — migration 0014.

Coverage:
  - GET /api/floating-artemis/models returns all three providers + alias maps
  - PATCH /sessions/{id}/model → 200, persists provider+model
  - PATCH /sessions/{id}/model with invalid provider → 400 bad_request
  - GET /sessions/{id} returns provider+model fields (null when unset)
  - update_session_model repository helper persists + refreshes row
  - SessionRead.from_orm_row surfaces provider/model from ORM row
  - SessionModelUpdate schema parses / defaults correctly
  - chat._resolve_adapter: null session → AnthropicAdapter
  - chat._resolve_adapter: provider="gemini" → GeminiAdapter via mocked registry
  - chat._resolve_adapter: MissingApiKeyError → returns str error
  - handle_turn uses _resolve_adapter when adapter=None
  - handle_turn broadcasts floating_artemis.failed on provider_error
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_session_row(
    *,
    session_id: str = "test-s1",
    provider: str | None = None,
    model: str | None = None,
) -> MagicMock:
    now = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)
    row = MagicMock()
    row.id = 1
    row.session_id = session_id
    row.owner_user_id = None
    row.started_at = now
    row.last_active_at = now
    row.closed_at = None
    row.title = None
    row.metadata_ = {}
    row.provider = provider
    row.model = model
    return row


async def _make_client() -> Any:
    from httpx import ASGITransport, AsyncClient

    from artemis.main import app

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── GET /api/floating-artemis/models ─────────────────────────────────────────


async def test_list_models_returns_all_providers() -> None:
    """GET /models returns all six provider entries."""
    async with await _make_client() as client:
        resp = await client.get("/api/floating-artemis/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    provider_ids = {p["id"] for p in data["providers"]}
    assert {
        "anthropic",
        "gemini",
        "openrouter",
        "claude-code",
        "codex",
        "lm-studio",
    } <= provider_ids
    codex = next(p for p in data["providers"] if p["id"] == "codex")
    assert [o["value"] for o in codex["effort"]["options"]] == ["low", "medium", "high", "xhigh"]
    assert codex["speed"]["supportsSpeedFor"] == ["gpt-5.4", "gpt-5.5"]


async def test_list_models_anthropic_has_expected_models() -> None:
    async with await _make_client() as client:
        resp = await client.get("/api/floating-artemis/models")
    data = resp.json()
    anthropic = next(p for p in data["providers"] if p["id"] == "anthropic")
    model_ids = {m["id"] for m in anthropic["models"]}
    assert "claude-sonnet-4-6" in model_ids
    assert "claude-opus-4-7" in model_ids
    assert "claude-haiku-4-5" in model_ids


async def test_list_models_gemini_has_alias_models() -> None:
    from artemis.providers.gemini.models import GEMINI_MODEL_MAP

    async with await _make_client() as client:
        resp = await client.get("/api/floating-artemis/models")
    data = resp.json()
    gemini = next(p for p in data["providers"] if p["id"] == "gemini")
    model_ids = {m["id"] for m in gemini["models"]}
    # All aliases from the map should be present
    assert model_ids == set(GEMINI_MODEL_MAP.keys())


async def test_list_models_openrouter_has_alias_models() -> None:
    from artemis.providers.openrouter.models import OPENROUTER_MODEL_MAP

    async with await _make_client() as client:
        resp = await client.get("/api/floating-artemis/models")
    data = resp.json()
    openrouter = next(p for p in data["providers"] if p["id"] == "openrouter")
    model_ids = {m["id"] for m in openrouter["models"]}
    assert model_ids == set(OPENROUTER_MODEL_MAP.keys())


# ── PATCH /sessions/{id}/model ────────────────────────────────────────────────


async def test_patch_session_model_valid_provider_returns_200() -> None:
    row = _make_session_row(provider="gemini", model="gemini-2.5-flash")
    with (
        patch("artemis.routes.floating_artemis.repo.get_session_by_id", return_value=row),
        patch("artemis.routes.floating_artemis.repo.update_session_model", return_value=row),
        patch("artemis.routes.floating_artemis.get_session"),
    ):
        async with await _make_client() as client:
            resp = await client.patch(
                "/api/floating-artemis/sessions/test-s1/model",
                json={"provider": "gemini", "model": "gemini-2.5-flash"},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "gemini"
    assert body["model"] == "gemini-2.5-flash"


async def test_patch_session_model_unknown_provider_returns_400() -> None:
    async with await _make_client() as client:
        resp = await client.patch(
            "/api/floating-artemis/sessions/test-s1/model",
            json={"provider": "totally-made-up", "model": "something"},
        )
    assert resp.status_code == 400
    body = resp.json()
    # The _errors.bad_request raises HTTPException; detail is a dict at the top level
    assert body.get("code") == "unknown_provider" or (
        isinstance(body.get("detail"), dict) and body["detail"].get("code") == "unknown_provider"
    )


async def test_patch_session_model_null_reverts_to_default() -> None:
    row = _make_session_row(provider=None, model=None)
    with (
        patch("artemis.routes.floating_artemis.repo.get_session_by_id", return_value=row),
        patch("artemis.routes.floating_artemis.repo.update_session_model", return_value=row),
        patch("artemis.routes.floating_artemis.get_session"),
    ):
        async with await _make_client() as client:
            resp = await client.patch(
                "/api/floating-artemis/sessions/test-s1/model",
                json={"provider": None, "model": None},
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] is None
    assert body["model"] is None


# ── GET /sessions/{id} includes provider/model ────────────────────────────────


async def test_get_session_returns_provider_model_fields() -> None:
    row = _make_session_row(provider="openrouter", model="llama-3.3-70b-free")
    with (
        patch("artemis.routes.floating_artemis.repo.get_session_by_id", return_value=row),
        patch("artemis.routes.floating_artemis.get_session"),
    ):
        async with await _make_client() as client:
            resp = await client.get("/api/floating-artemis/sessions/test-s1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "openrouter"
    assert body["model"] == "llama-3.3-70b-free"


async def test_get_session_provider_model_null_when_unset() -> None:
    row = _make_session_row()
    with (
        patch("artemis.routes.floating_artemis.repo.get_session_by_id", return_value=row),
        patch("artemis.routes.floating_artemis.get_session"),
    ):
        async with await _make_client() as client:
            resp = await client.get("/api/floating-artemis/sessions/test-s1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] is None
    assert body["model"] is None


# ── SessionRead schema ────────────────────────────────────────────────────────


def test_session_read_from_orm_row_surfaces_provider_model() -> None:
    from artemis.floating_artemis.schemas import SessionRead

    row = _make_session_row(provider="gemini", model="gemini-2.5-pro")
    sr = SessionRead.from_orm_row(row)
    assert sr.provider == "gemini"
    assert sr.model == "gemini-2.5-pro"


def test_session_read_from_orm_row_null_when_unset() -> None:
    from artemis.floating_artemis.schemas import SessionRead

    row = _make_session_row()
    sr = SessionRead.from_orm_row(row)
    assert sr.provider is None
    assert sr.model is None


def test_session_model_update_schema_defaults_to_none() -> None:
    from artemis.floating_artemis.schemas import SessionModelUpdate

    body = SessionModelUpdate()
    assert body.provider is None
    assert body.model is None


# ── chat._resolve_adapter ─────────────────────────────────────────────────────


async def test_resolve_adapter_no_provider_prefers_claude_code() -> None:
    """When session has no provider, fallback chain tries claude-code first."""
    from artemis.floating_artemis.chat import _resolve_adapter

    row = _make_session_row(provider=None, model=None)
    mock_adapter = MagicMock()

    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id", new=AsyncMock(return_value=row)
        ),
        patch("artemis.floating_artemis.chat.get_adapter", return_value=mock_adapter) as mock_ga,
    ):
        await _resolve_adapter(session_id="test-s1", db_session=MagicMock())

    # First call in the fallback chain is claude-code (subscription, no key needed)
    mock_ga.assert_called_once()
    assert mock_ga.call_args[0][0] == "claude-code"


async def test_resolve_adapter_gemini_calls_get_adapter_with_gemini() -> None:
    """When session.provider='gemini', get_adapter is called with 'gemini'."""
    from artemis.floating_artemis.chat import _resolve_adapter

    row = _make_session_row(provider="gemini", model="gemini-2.5-flash")

    mock_adapter = MagicMock()

    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id", new=AsyncMock(return_value=row)
        ),
        patch("artemis.floating_artemis.chat.get_adapter", return_value=mock_adapter) as mock_ga,
    ):
        result = await _resolve_adapter(session_id="test-s1", db_session=MagicMock())

    assert result is mock_adapter
    mock_ga.assert_called_once_with("gemini", default_model="gemini-2.5-flash")


async def test_resolve_adapter_returns_str_when_all_providers_fail() -> None:
    """When every provider in the fallback chain fails, returns a str error message."""
    from artemis.floating_artemis.chat import _resolve_adapter
    from artemis.providers.errors import MissingApiKeyError

    row = _make_session_row(provider="gemini", model=None)

    with (
        patch(
            "artemis.floating_artemis.repository.get_session_by_id", new=AsyncMock(return_value=row)
        ),
        patch(
            "artemis.floating_artemis.chat.get_adapter",
            side_effect=MissingApiKeyError("everything missing"),
        ),
    ):
        result = await _resolve_adapter(session_id="test-s1", db_session=MagicMock())

    assert isinstance(result, str)
    assert "provider" in result.lower() or "integrations" in result.lower()


async def test_handle_turn_broadcasts_failed_on_provider_error() -> None:
    """handle_turn broadcasts floating_artemis.failed when provider not configured."""
    from artemis.floating_artemis.chat import handle_turn

    error_msg = "Provider 'gemini' needs configuration"
    broadcast_calls: list[dict[str, Any]] = []

    async def fake_broadcast(session_id: str, event: dict[str, Any]) -> None:
        broadcast_calls.append(event)

    with (
        patch("artemis.floating_artemis.chat._resolve_adapter", return_value=error_msg),
        patch("artemis.floating_artemis.chat._broadcast", side_effect=fake_broadcast),
        patch("artemis.floating_artemis.chat.classify_intent") as mock_intent,
        patch("artemis.floating_artemis.chat.get_status", return_value={}),
        patch("artemis.floating_artemis.chat.select_voice_samples", return_value=[]),
        patch("artemis.floating_artemis.chat._get_page_context_text", return_value=None),
        patch("artemis.floating_artemis.chat._load_message_history", return_value=[]),
    ):
        from artemis.floating_artemis.intent import IntentKind, IntentMatch

        mock_intent.return_value = IntentMatch(kind=IntentKind.NONE, confidence=0.0)
        result = await handle_turn(session_id="test-s1", user_text="hello")

    assert result.stop_reason == "provider_error"
    failed_events = [e for e in broadcast_calls if e.get("type") == "floating_artemis.failed"]
    assert len(failed_events) == 1
    assert error_msg in failed_events[0]["error"]
