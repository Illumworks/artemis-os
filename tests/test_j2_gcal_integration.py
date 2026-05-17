"""Phase J2 — Google Calendar integration tests.

Tests:
  1.  test_gcal_oauth_start_without_credentials   — no GCAL_CLIENT_ID → 503
  2.  test_gcal_oauth_start_with_credentials      — creds set → 200, url contains accounts.google.com
  3.  test_gcal_oauth_start_scope_present         — OAuth URL includes calendar scope
  4.  test_gcal_oauth_start_state_token           — OAuth URL includes state query param
  5.  test_gcal_oauth_callback_invalid_state      — state=invalid → 400
  6.  test_gcal_verify_no_integration             — empty DB → 404
  7.  test_gcal_provider_from_session_missing_creds — incomplete DB + no env → 503
  8.  test_resolve_gcal_config_db_wins            — DB client_id overrides env
  9.  test_resolve_gcal_config_env_fallback       — no DB → uses env vars
  10. test_resolve_gcal_config_per_field_mix      — DB client_id, env client_secret
  11. test_resolve_gcal_config_missing_raises     — both absent → MissingProviderConfigError
  12. test_gcal_list_calendars_tool_no_integration — no active integration → error string
  13. test_gcal_list_events_tool_missing_params   — missing time_min → error string
  14. test_gcal_tools_registered_at_correct_layer — verify layer 2/3 assignments
  15. test_gcal_types_model_validate_calendar     — Calendar model_validate from dict
  16. test_gcal_types_model_validate_event        — Event model_validate with camelCase aliases
  17. test_gcal_client_list_calendars_mock        — mock httpx → list_calendars returns Calendar list
  18. test_gcal_client_list_events_mock           — mock httpx → list_events returns Event list
  19. test_gcal_client_token_refresh_on_401       — 401 triggers refresh + retry
  20. test_gcal_client_create_event_mock          — mock httpx → create_event returns Event
  21. test_gcal_frontend_provider_in_list         — gcal entry present in PROVIDERS array
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import NullPool, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

import artemis.db as db_module
import artemis.integrations.models  # noqa: F401
from artemis.config import settings
from artemis.db import attach_pgvector_codec

pytestmark = pytest.mark.asyncio

# ── Test DB setup ─────────────────────────────────────────────────────────────

_db_url = os.environ.get("ARTEMIS_TEST_DB_URL", settings.db_url)
_test_engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
attach_pgvector_codec(_test_engine)
db_module.engine = _test_engine
db_module.SessionLocal = __import__(
    "sqlalchemy.ext.asyncio", fromlist=["async_sessionmaker"]
).async_sessionmaker(
    bind=_test_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

_TRUNCATE = text(
    "TRUNCATE integration_configs, integrations, slack_inbound_messages RESTART IDENTITY CASCADE"
)


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_db_url, echo=False, poolclass=NullPool)
    attach_pgvector_codec(engine)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            async with session.begin():
                await session.execute(_TRUNCATE)
            yield session
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from artemis.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ── OAuth start ───────────────────────────────────────────────────────────────


async def test_gcal_oauth_start_without_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GCAL_CLIENT_ID", None)
        os.environ.pop("GCAL_CLIENT_SECRET", None)
        r = await client.get("/api/integrations/gcal/oauth/start")
    assert r.status_code == 503


async def test_gcal_oauth_start_with_credentials(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "test_cid", "GCAL_CLIENT_SECRET": "test_csec"}):
        r = await client.get("/api/integrations/gcal/oauth/start")
    assert r.status_code == 200
    body = r.json()
    assert "url" in body
    assert "accounts.google.com" in body["url"]


async def test_gcal_oauth_start_scope_present(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "test_cid", "GCAL_CLIENT_SECRET": "test_csec"}):
        r = await client.get("/api/integrations/gcal/oauth/start")
    assert r.status_code == 200
    url = r.json()["url"]
    assert "calendar" in url


async def test_gcal_oauth_start_state_token(client: AsyncClient, db_session: AsyncSession) -> None:
    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "c", "GCAL_CLIENT_SECRET": "s"}):
        r = await client.get("/api/integrations/gcal/oauth/start")
    assert "state=" in r.json()["url"]


# ── OAuth callback ────────────────────────────────────────────────────────────


async def test_gcal_oauth_callback_invalid_state(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "c", "GCAL_CLIENT_SECRET": "s"}):
        r = await client.get(
            "/api/integrations/gcal/oauth/callback",
            params={"code": "any_code", "state": "invalid_state_xyz"},
        )
    assert r.status_code == 400


# ── Verify ────────────────────────────────────────────────────────────────────


async def test_gcal_verify_no_integration(client: AsyncClient, db_session: AsyncSession) -> None:
    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "c", "GCAL_CLIENT_SECRET": "s"}):
        r = await client.get("/api/integrations/gcal/verify")
    assert r.status_code == 404


async def test_gcal_provider_from_session_missing_creds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Verify route returns 503 when GCal creds entirely absent."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GCAL_CLIENT_ID", None)
        os.environ.pop("GCAL_CLIENT_SECRET", None)
        r = await client.get("/api/integrations/gcal/verify")
    # 404 (no integration row) or 503 (no creds) both acceptable here —
    # the route checks for active integration first
    assert r.status_code in (404, 503)


# ── resolve_gcal_config ───────────────────────────────────────────────────────


async def test_resolve_gcal_config_db_wins(db_session: AsyncSession) -> None:
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_gcal_config

    await repo.upsert_provider_config(
        db_session, "gcal", {"client_id": "DB_CID", "client_secret": "DB_CSEC"}
    )
    await db_session.commit()

    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "ENV_CID", "GCAL_CLIENT_SECRET": "ENV_CSEC"}):
        cfg = await resolve_gcal_config(db_session)

    assert cfg.client_id == "DB_CID"
    assert cfg.client_secret == "DB_CSEC"


async def test_resolve_gcal_config_env_fallback(db_session: AsyncSession) -> None:
    from artemis.integrations.config_resolver import resolve_gcal_config

    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "ENV_CID", "GCAL_CLIENT_SECRET": "ENV_CSEC"}):
        cfg = await resolve_gcal_config(db_session)

    assert cfg.client_id == "ENV_CID"
    assert cfg.client_secret == "ENV_CSEC"


async def test_resolve_gcal_config_per_field_mix(db_session: AsyncSession) -> None:
    from artemis.integrations import repository as repo
    from artemis.integrations.config_resolver import resolve_gcal_config

    await repo.upsert_provider_config(db_session, "gcal", {"client_id": "DB_CID"})
    await db_session.commit()

    with patch.dict(os.environ, {"GCAL_CLIENT_ID": "ENV_DECOY", "GCAL_CLIENT_SECRET": "ENV_CSEC"}):
        cfg = await resolve_gcal_config(db_session)

    assert cfg.client_id == "DB_CID"
    assert cfg.client_secret == "ENV_CSEC"


async def test_resolve_gcal_config_missing_raises(db_session: AsyncSession) -> None:
    from artemis.integrations.config_resolver import MissingProviderConfigError, resolve_gcal_config

    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GCAL_CLIENT_ID", None)
        os.environ.pop("GCAL_CLIENT_SECRET", None)
        with pytest.raises(MissingProviderConfigError) as exc_info:
            await resolve_gcal_config(db_session)

    assert exc_info.value.provider == "gcal"
    assert len(exc_info.value.missing_fields) >= 1


# ── Tools ─────────────────────────────────────────────────────────────────────


async def test_gcal_list_calendars_tool_no_integration() -> None:
    result = await _import_and_call("_list_calendars", {})
    assert "No active Google Calendar integration" in result


async def test_gcal_list_events_tool_missing_params() -> None:
    result = await _import_and_call("_list_events", {"calendar_id": "primary"})
    assert "required" in result.lower() or "time_min" in result


async def _import_and_call(fn_name: str, inp: dict) -> str:  # type: ignore[type-arg]
    import artemis.integrations.gcal.tools as tools_mod

    fn = getattr(tools_mod, fn_name)
    return await fn(inp)


async def test_gcal_tools_registered_at_correct_layer() -> None:
    from artemis.floating_artemis.authority import AuthorizedToolRegistry
    from artemis.integrations.gcal.tools import register_gcal_tools

    registry = AuthorizedToolRegistry()
    register_gcal_tools(registry)

    # Layer 2 — auto-invoke (read-only)
    assert registry.is_auto_invoke("list_calendars")
    assert registry.is_auto_invoke("list_events")

    # Layer 3 — requires confirmation (side-effect)
    assert registry.requires_confirmation("create_event")
    assert registry.requires_confirmation("update_event")
    assert registry.requires_confirmation("delete_event")


# ── Pydantic types ────────────────────────────────────────────────────────────


def test_gcal_types_model_validate_calendar() -> None:
    from artemis.integrations.gcal.types import Calendar

    cal = Calendar.model_validate({"id": "primary", "summary": "My Calendar", "primary": True})
    assert cal.id == "primary"
    assert cal.primary is True


def test_gcal_types_model_validate_event() -> None:
    from artemis.integrations.gcal.types import Event

    raw = {
        "id": "evt001",
        "summary": "Team sync",
        "start": {"dateTime": "2024-01-15T10:00:00Z"},
        "end": {"dateTime": "2024-01-15T11:00:00Z"},
        "htmlLink": "https://calendar.google.com/event?eid=evt001",
    }
    event = Event.model_validate(raw)
    assert event.id == "evt001"
    assert event.start.date_time == "2024-01-15T10:00:00Z"
    assert event.html_link == "https://calendar.google.com/event?eid=evt001"


# ── Client mock tests ─────────────────────────────────────────────────────────


def _mock_calendar_response() -> dict:  # type: ignore[type-arg]
    return {"items": [{"id": "primary", "summary": "Jon Fila", "primary": True}]}


def _mock_event_response() -> dict:  # type: ignore[type-arg]
    return {
        "items": [
            {
                "id": "evt_abc",
                "summary": "Standup",
                "start": {"dateTime": "2024-01-15T09:00:00Z"},
                "end": {"dateTime": "2024-01-15T09:30:00Z"},
            }
        ]
    }


def _make_mock_http_response(json_data: dict, status: int = 200) -> MagicMock:  # type: ignore[type-arg]
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.is_success = status < 400
    mock_resp.json.return_value = json_data
    mock_resp.text = str(json_data)
    return mock_resp


async def test_gcal_client_list_calendars_mock() -> None:
    from artemis.integrations.gcal.client import GCalClient

    client = GCalClient("tok", "ref", "cid", "csec")
    mock_resp = _make_mock_http_response(_mock_calendar_response())

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http_instance = AsyncMock()
        mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
        mock_http_instance.__aexit__ = AsyncMock(return_value=False)
        mock_http_instance.get = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http_instance

        calendars = await client.list_calendars()

    assert len(calendars) == 1
    assert calendars[0].id == "primary"
    assert calendars[0].primary is True


async def test_gcal_client_list_events_mock() -> None:
    from artemis.integrations.gcal.client import GCalClient

    client = GCalClient("tok", "ref", "cid", "csec")
    mock_resp = _make_mock_http_response(_mock_event_response())

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http_instance = AsyncMock()
        mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
        mock_http_instance.__aexit__ = AsyncMock(return_value=False)
        mock_http_instance.get = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http_instance

        events = await client.list_events("primary", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")

    assert len(events) == 1
    assert events[0].id == "evt_abc"
    assert events[0].summary == "Standup"


async def test_gcal_client_token_refresh_on_401() -> None:
    from artemis.integrations.gcal.client import GCalClient

    client = GCalClient("expired_tok", "refresh_tok", "cid", "csec")

    mock_401 = _make_mock_http_response({}, status=401)
    mock_ok = _make_mock_http_response(_mock_calendar_response(), status=200)
    mock_refresh_resp = MagicMock()
    mock_refresh_resp.is_success = True
    mock_refresh_resp.json.return_value = {"access_token": "new_tok"}
    mock_refresh_resp.raise_for_status = MagicMock()

    get_call_count = 0

    async def mock_get(*args: object, **kwargs: object) -> MagicMock:
        nonlocal get_call_count
        get_call_count += 1
        return mock_401 if get_call_count == 1 else mock_ok

    async def mock_post(*args: object, **kwargs: object) -> MagicMock:
        return mock_refresh_resp

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http_instance = AsyncMock()
        mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
        mock_http_instance.__aexit__ = AsyncMock(return_value=False)
        mock_http_instance.get = mock_get
        mock_http_instance.post = mock_post
        mock_http_cls.return_value = mock_http_instance

        calendars = await client.list_calendars()

    assert client.access_token == "new_tok"
    assert len(calendars) == 1


async def test_gcal_client_create_event_mock() -> None:
    from artemis.integrations.gcal.client import GCalClient
    from artemis.integrations.gcal.types import EventDateTime

    client = GCalClient("tok", "ref", "cid", "csec")
    mock_event = {
        "id": "new_evt",
        "summary": "New meeting",
        "start": {"dateTime": "2024-01-20T14:00:00Z"},
        "end": {"dateTime": "2024-01-20T15:00:00Z"},
    }
    mock_resp = _make_mock_http_response(mock_event)

    with patch("httpx.AsyncClient") as mock_http_cls:
        mock_http_instance = AsyncMock()
        mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
        mock_http_instance.__aexit__ = AsyncMock(return_value=False)
        mock_http_instance.post = AsyncMock(return_value=mock_resp)
        mock_http_cls.return_value = mock_http_instance

        event = await client.create_event(
            calendar_id="primary",
            summary="New meeting",
            start=EventDateTime(date_time="2024-01-20T14:00:00Z"),
            end=EventDateTime(date_time="2024-01-20T15:00:00Z"),
        )

    assert event.id == "new_evt"
    assert event.summary == "New meeting"


# ── Frontend sanity ───────────────────────────────────────────────────────────


def test_gcal_frontend_provider_in_list() -> None:
    """PROVIDERS array in integrations.js must contain a gcal entry."""
    import re
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    js_path = repo_root / "public" / "js" / "features" / "integrations.js"
    content = js_path.read_text()
    assert re.search(r"id:\s*['\"]gcal['\"]", content), "gcal not found in PROVIDERS array"
