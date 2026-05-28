"""Tests for the free-API scout source tools — MockTransport, no live HTTP.

Covers grants_gov.search, federal_register.search, legiscan.search and
legiscan.get_bill. Mocking style mirrors test_state_doe.py: monkeypatch
``ScoutHttpClient.__init__`` to inject a MockTransport-backed inner client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from artemis.tools.context import ToolContext
from artemis.tools.federal_register import _factory as fed_factory
from artemis.tools.grants_gov import _factory as grants_factory
from artemis.tools.legiscan import _factory_get_bill, _factory_search
from artemis.tools.registry import known_tool_names

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


class _FakeSession:
    pass


def _ctx() -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id="marketing.scout.free_api",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _load_fixture(name: str) -> bytes:
    return (_FIXTURE_DIR / name).read_bytes()


def _mock_http(monkeypatch: pytest.MonkeyPatch, status: int, content: bytes) -> None:
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(status, content=content))
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)


# --- grants_gov.search -------------------------------------------------------


@pytest.mark.asyncio
async def test_grants_gov_search_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_http(monkeypatch, 200, _load_fixture("grants_gov_sample.json"))
    _, impl = grants_factory(_ctx())
    items = json.loads(await impl({"keyword": "literacy", "rows": 25}))
    assert len(items) >= 1
    first = items[0]
    assert first["title"]
    assert first["opportunityNumber"]
    assert set(first) == {"title", "opportunityNumber", "closeDate", "agency", "url"}
    assert first["url"].startswith("https://www.grants.gov/")


@pytest.mark.asyncio
async def test_grants_gov_search_empty_keyword() -> None:
    _, impl = grants_factory(_ctx())
    assert json.loads(await impl({"keyword": ""})) == []


@pytest.mark.asyncio
async def test_grants_gov_search_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_http(monkeypatch, 500, b"server error")
    _, impl = grants_factory(_ctx())
    assert json.loads(await impl({"keyword": "literacy"})) == []


# --- federal_register.search -------------------------------------------------


@pytest.mark.asyncio
async def test_federal_register_search_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_http(monkeypatch, 200, _load_fixture("federal_register_sample.json"))
    _, impl = fed_factory(_ctx())
    items = json.loads(await impl({"term": "literacy education", "per_page": 20}))
    assert len(items) >= 1
    first = items[0]
    assert first["title"]
    assert first["document_number"]
    assert isinstance(first["agencies"], list)
    assert set(first) == {
        "title",
        "document_number",
        "publication_date",
        "agencies",
        "html_url",
        "abstract",
    }


@pytest.mark.asyncio
async def test_federal_register_search_empty_term() -> None:
    _, impl = fed_factory(_ctx())
    assert json.loads(await impl({"term": ""})) == []


@pytest.mark.asyncio
async def test_federal_register_search_http_500(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_http(monkeypatch, 500, b"server error")
    _, impl = fed_factory(_ctx())
    assert json.loads(await impl({"term": "literacy"})) == []


# --- legiscan.search / get_bill ---------------------------------------------

_STUB = (
    "STUB: LegiScan needs a free API key — register at legiscan.com/legiscan "
    "and add LEGISCAN_API_KEY in the Connectors panel."
)


@pytest.mark.asyncio
async def test_legiscan_search_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
    _, impl = _factory_search(_ctx())
    assert await impl({"query": "literacy"}) == _STUB


@pytest.mark.asyncio
async def test_legiscan_get_bill_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LEGISCAN_API_KEY", raising=False)
    _, impl = _factory_get_bill(_ctx())
    assert await impl({"billId": "1849621"}) == _STUB


@pytest.mark.asyncio
async def test_legiscan_search_with_key_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGISCAN_API_KEY", "test-key")
    _mock_http(monkeypatch, 200, _load_fixture("legiscan_search_sample.json"))
    _, impl = _factory_search(_ctx())
    items = json.loads(await impl({"query": "dyslexia screening", "state": "ALL"}))
    assert len(items) == 2
    assert items[0]["bill_id"] == 1849621
    assert items[0]["relevance"] == 100
    assert items[0]["change_hash"]


@pytest.mark.asyncio
async def test_legiscan_get_bill_with_key_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEGISCAN_API_KEY", "test-key")
    bill_json = json.dumps(
        {
            "status": "OK",
            "bill": {
                "bill_id": 1849621,
                "bill_number": "HB1004",
                "title": "Universal Dyslexia Screening For Early Learners",
                "description": "Concerning literacy screening in early grades.",
                "state": "CO",
                "status_date": "2026-02-01",
                "url": "https://legiscan.com/CO/bill/HB1004/2026",
                "history": [
                    {"date": "2026-01-10", "action": "Introduced In House"},
                    {"date": "2026-02-01", "action": "House Committee Report"},
                ],
            },
        }
    ).encode("utf-8")
    _mock_http(monkeypatch, 200, bill_json)
    _, impl = _factory_get_bill(_ctx())
    bill = json.loads(await impl({"billId": "1849621"}))
    assert bill["bill_id"] == 1849621
    assert bill["bill_number"] == "HB1004"
    assert bill["title"] == "Universal Dyslexia Screening For Early Learners"
    assert bill["state"] == "CO"
    assert bill["last_action"] == "House Committee Report"


# --- registration ------------------------------------------------------------


def test_tools_registered() -> None:
    names = known_tool_names()
    for name in (
        "grants_gov.search",
        "federal_register.search",
        "legiscan.search",
        "legiscan.get_bill",
    ):
        assert name in names
