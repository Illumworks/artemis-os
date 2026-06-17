"""Tests for the usaspending.search tool — MockTransport, no live HTTP.

Mocking style mirrors test_free_api_sources.py: monkeypatch
``ScoutHttpClient.__init__`` to inject a MockTransport-backed inner client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from artemis.tools.context import ToolContext
from artemis.tools.registry import known_tool_names
from artemis.tools.usaspending import (
    _DETAIL_BASE,
    _factory,
    _parse_result,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


class _FakeSession:
    pass


def _ctx() -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id="marketing.scout.federal_funding",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _mock_http(monkeypatch: pytest.MonkeyPatch, status: int, content: bytes) -> None:
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, content=content))
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)


def _load_fixture(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


# ---------------------------------------------------------------------------
# _parse_result unit tests
# ---------------------------------------------------------------------------


def test_parse_result_full() -> None:
    raw = {
        "Award ID": "S010A250043",
        "Recipient Name": "TX EDUCATION AGENCY",
        "recipient_location_state_code": "TX",
        "Award Amount": 1808606075.0,
        "cfda_number": "84.010",
        "cfda_program_title": "TITLE I GRANTS TO LOCAL EDUCATIONAL AGENCIES",
        "Start Date": "2025-07-01",
        "End Date": "2026-09-30",
        "Last Modified Date": "2025-10-30 19:18:28",
        "Description": "TITLE I PART A BASIC GRANTS TO LEAS",
        "generated_internal_id": "ASST_NON_S010A250043_091",
    }
    parsed = _parse_result(raw)
    assert parsed["award_id"] == "S010A250043"
    assert parsed["recipient_name"] == "TX EDUCATION AGENCY"
    assert parsed["recipient_state"] == "TX"
    assert parsed["cfda_number"] == "84.010"
    assert parsed["cfda_program_title"] == "TITLE I GRANTS TO LOCAL EDUCATIONAL AGENCIES"
    assert parsed["amount"] == 1808606075.0
    assert parsed["start_date"] == "2025-07-01"
    assert parsed["end_date"] == "2026-09-30"
    assert parsed["last_modified_date"] == "2025-10-30 19:18:28"
    assert parsed["description"] == "TITLE I PART A BASIC GRANTS TO LEAS"
    assert parsed["url"] == f"{_DETAIL_BASE}ASST_NON_S010A250043_091"


def test_parse_result_missing_fields() -> None:
    """Missing/null fields should produce empty strings or None, not raise."""
    raw: dict[str, Any] = {
        "Award ID": None,
        "Recipient Name": "",
        "recipient_location_state_code": None,
        "Award Amount": None,
        "cfda_number": None,
        "cfda_program_title": None,
        "Start Date": None,
        "End Date": None,
        "Last Modified Date": None,
        "Description": None,
        "generated_internal_id": "",
    }
    parsed = _parse_result(raw)
    assert parsed["award_id"] == ""
    assert parsed["recipient_state"] == ""
    assert parsed["cfda_number"] == ""
    assert parsed["amount"] is None
    assert parsed["start_date"] is None
    assert parsed["end_date"] is None
    assert parsed["last_modified_date"] is None
    assert parsed["url"] == ""  # empty generated_internal_id → no URL


# ---------------------------------------------------------------------------
# _impl integration tests (MockTransport)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usaspending_search_parses_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: fixture data parses into the correct output shape."""
    _mock_http(monkeypatch, 200, _load_fixture("usaspending_sample.json"))
    _, impl = _factory(_ctx())
    items = json.loads(await impl({}))
    assert isinstance(items, list)
    assert len(items) == 5

    # Verify required keys on every item.
    required_keys = {
        "award_id",
        "recipient_name",
        "recipient_state",
        "cfda_number",
        "cfda_program_title",
        "amount",
        "start_date",
        "end_date",
        "last_modified_date",
        "description",
        "url",
    }
    for item in items:
        assert set(item.keys()) == required_keys, f"unexpected keys in {item}"

    first = items[0]
    assert first["award_id"] == "S010A250043"
    assert first["recipient_name"] == "TX EDUCATION AGENCY"
    assert first["recipient_state"] == "TX"
    assert first["cfda_number"] == "84.010"
    assert first["amount"] == 1808606075.0
    assert first["url"].startswith("https://www.usaspending.gov/award/")


@pytest.mark.asyncio
async def test_usaspending_search_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTP 500 → graceful empty list, no raise."""
    _mock_http(monkeypatch, 500, b"server error")
    _, impl = _factory(_ctx())
    result = json.loads(await impl({}))
    assert result == []


@pytest.mark.asyncio
async def test_usaspending_search_bad_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON body → graceful empty list."""
    _mock_http(monkeypatch, 200, b"not json{{")
    _, impl = _factory(_ctx())
    result = json.loads(await impl({}))
    assert result == []


@pytest.mark.asyncio
async def test_usaspending_search_empty_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """API returns 200 but empty results list → empty list."""
    payload = json.dumps({"results": [], "page_metadata": {"page": 1, "hasNext": False}}).encode()
    _mock_http(monkeypatch, 200, payload)
    _, impl = _factory(_ctx())
    result = json.loads(await impl({}))
    assert result == []


@pytest.mark.asyncio
async def test_usaspending_search_state_filter_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom states argument accepted without error."""
    _mock_http(monkeypatch, 200, _load_fixture("usaspending_sample.json"))
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"states": ["TX", "FL"], "limit": 10}))
    assert isinstance(items, list)


@pytest.mark.asyncio
async def test_usaspending_search_custom_lookback(monkeypatch: pytest.MonkeyPatch) -> None:
    """lookback_days and limit coercion: valid values accepted."""
    _mock_http(monkeypatch, 200, _load_fixture("usaspending_sample.json"))
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"lookback_days": 90, "limit": 3}))
    assert isinstance(items, list)
    # fixture has 5 items, but limit=3 is passed to API (mock returns all 5)
    # so at most fixture count; no error
    assert len(items) <= 5


@pytest.mark.asyncio
async def test_usaspending_search_limit_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Limit above 100 is clamped to 100 (no error)."""
    _mock_http(monkeypatch, 200, _load_fixture("usaspending_sample.json"))
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"limit": 9999}))
    assert isinstance(items, list)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_usaspending_tool_registered() -> None:
    """usaspending.search must appear in the global tool registry."""
    assert "usaspending.search" in known_tool_names()
