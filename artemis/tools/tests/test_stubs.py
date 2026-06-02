"""Tests for stub tools — verify placeholder strings and registration."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import artemis.tools  # noqa: F401 — ensure all tools are registered
from artemis.tools.board_minutes import _factory as _bm_factory
from artemis.tools.contact_db import _factory as _contact_factory
from artemis.tools.context import ToolContext
from artemis.tools.federal_register import _factory as _fed_factory
from artemis.tools.grants_gov import _factory as _grants_factory
from artemis.tools.legiscan import _factory_get_bill as _legiscan_bill
from artemis.tools.legiscan import _factory_search as _legiscan_search
from artemis.tools.linkedin import _factory_delta as _li_delta
from artemis.tools.linkedin import _factory_fetch as _li_fetch
from artemis.tools.memory_layer import _factory_get, _factory_similarity, _factory_upsert
from artemis.tools.procurement import _factory as _proc_factory
from artemis.tools.registry import known_tool_names
from artemis.tools.starbridge import _factory_get_doc as _sb_doc
from artemis.tools.starbridge import _factory_search as _sb_search
from artemis.tools.unresolved_signals import _factory as _unresolved_factory


class _FakeSession:
    pass


def _ctx(agent_id: str = "marketing.scout.regional_news") -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _ctx_with_mock_session(
    agent_id: str = "marketing.scout.regional_news",
    scalar_one_or_none_return: Any = None,
    scalars_all_return: list[Any] | None = None,
) -> ToolContext:
    """Return a ToolContext whose session is a mock that handles execute() calls.

    scalar_one_or_none_return: returned by .scalar_one_or_none() on execute result (for
      DistrictContact.id query).
    scalars_all_return: returned by .scalars().all() on execute result (for
      SignalQueue.resolved_district_id query).
    """
    mock_session = MagicMock()

    # execute() is async; create a mock result that supports both access patterns
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = scalar_one_or_none_return
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = scalars_all_return or []
    mock_result.scalars.return_value = mock_scalars

    mock_session.execute = AsyncMock(return_value=mock_result)

    return ToolContext(
        session=mock_session,
        agent_id=agent_id,
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


@pytest.mark.asyncio
async def test_memory_layer_stubs() -> None:
    _, impl_u = _factory_upsert(_ctx())
    assert await impl_u({"districtId": "d1"}) == "ok-stub"
    _, impl_g = _factory_get(_ctx())
    assert await impl_g({"districtId": "d1"}) == "[]"
    _, impl_s = _factory_similarity(_ctx())
    assert await impl_s({}) == "0.0"


@pytest.mark.asyncio
async def test_contact_db_stub_numeric_true() -> None:
    """Numeric districtId with an existing active contact → 'true'."""
    # scalar_one_or_none returns a contact id (non-None) → has_contact returns "true"
    ctx = _ctx_with_mock_session(scalar_one_or_none_return=42)
    _, impl = _contact_factory(ctx)
    assert await impl({"districtId": "123"}) == "true"


@pytest.mark.asyncio
async def test_contact_db_stub_numeric_false() -> None:
    """Numeric districtId with no active contact → 'false'."""
    ctx = _ctx_with_mock_session(scalar_one_or_none_return=None)
    _, impl = _contact_factory(ctx)
    assert await impl({"districtId": "123"}) == "false"


@pytest.mark.asyncio
async def test_contact_db_stub_text_id_no_signal() -> None:
    """Non-numeric districtId with no matching signal_queue row → 'false'."""
    # scalars().all() returns [] (no resolved_district_id for text id "d1")
    ctx = _ctx_with_mock_session(scalars_all_return=[])
    _, impl = _contact_factory(ctx)
    assert await impl({"districtId": "d1"}) == "false"


@pytest.mark.asyncio
async def test_unresolved_signals_stub() -> None:
    _, impl = _unresolved_factory(_ctx())
    result = await impl({"reason": "bad payload"})
    assert "STUB" in result and "unresolved_signals" in result


@pytest.mark.asyncio
async def test_api_key_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """All API-key-required stubs return STUB string."""
    # These are real-but-stub-until-key; clear every gating env var so the test is
    # hermetic regardless of what's in the developer's .env (e.g. a live SAM_API_KEY).
    for _var in (
        "LEGISCAN_API_KEY",
        "SAM_API_KEY",
        "PROCUREMENT_PORTAL_URL",
        "STARBRIDGE_API_KEY",
        "LINKEDIN_API_KEY",
    ):
        monkeypatch.delenv(_var, raising=False)
    stubs = [
        _legiscan_search(_ctx()),
        _legiscan_bill(_ctx()),
        _sb_search(_ctx()),
        _sb_doc(_ctx()),
        _proc_factory(_ctx()),
        _li_fetch(_ctx()),
        _li_delta(_ctx()),
    ]
    for _, impl in stubs:
        result = await impl({})
        assert "STUB" in result, f"Expected STUB in: {result}"


@pytest.mark.asyncio
async def test_real_free_tools_graceful_empty() -> None:
    """grants_gov + federal_register are now real, no key — empty args → []."""
    _, grants = _grants_factory(_ctx())
    assert json.loads(await grants({})) == []
    _, fed = _fed_factory(_ctx())
    assert json.loads(await fed({})) == []


@pytest.mark.asyncio
async def test_board_minutes_no_url() -> None:
    _, impl = _bm_factory(_ctx())
    result = await impl({"district": {}})
    assert json.loads(result) == []


def test_registry_completeness() -> None:
    """Registry must contain ≥20 tools, all catalog entries present."""
    names = known_tool_names()
    required = [
        "campaign_brief.write",
        "contact_db_stub.has_contact",
        "federal_register.search",
        "grants_gov.search",
        "legiscan.get_bill",
        "legiscan.search",
        "linkedin_scraper.check_profile_delta",
        "linkedin_scraper.fetch_posts",
        "memory_layer.compute_similarity",
        "memory_layer.get",
        "memory_layer.upsert_last_seen",
        "news_api.search",
        "pdf_extractor.extract",
        "procurement_portal.fetch",
        "reason_codes.get_allowlist",
        "reason_codes.lookup",
        "signal_queue.write",
        "starbridge.get_document",
        "starbridge.search",
        "state_doe.fetch",
        "territory_config.get_priority_states",
        "territory_config.get_watch_keywords",
        "unresolved_signals.write",
        "board_minutes.fetch",
    ]
    for name in required:
        assert name in names, f"Missing: {name!r}"
    assert len(names) >= 20
