"""Tests for procurement_portal.fetch — MockTransport, no live HTTP."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from artemis.scouts._http import ScoutHttpClient
from artemis.tools.context import ToolContext
from artemis.tools.procurement import _factory

_STUB = "STUB: SAM.gov opportunities need an api.data.gov key. Set SAM_API_KEY in the .env file."
_ORIG_SCOUT_HTTP_INIT = ScoutHttpClient.__init__


class _FakeSession:
    pass


def _ctx() -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id="marketing.scout.procurement",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _mock_http(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        _ORIG_SCOUT_HTTP_INIT(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)


@pytest.mark.asyncio
async def test_procurement_no_key_returns_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SAM_API_KEY", raising=False)
    _, impl = _factory(_ctx())
    assert await impl({"query": "literacy"}) == _STUB


@pytest.mark.asyncio
async def test_procurement_parses_projected_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAM_API_KEY", "test-key")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["keyword"] = request.url.params.get("keyword", "")
        seen["postedFrom"] = request.url.params.get("postedFrom", "")
        seen["postedTo"] = request.url.params.get("postedTo", "")
        seen["limit"] = request.url.params.get("limit", "")
        seen["ptype"] = request.url.params.get("ptype", "")
        seen["ncode"] = request.url.params.get("ncode", "")
        payload = {
            "totalRecords": 1,
            "limit": 1,
            "offset": 0,
            "opportunitiesData": [
                {
                    "title": "K-12 Literacy Tutoring Services",
                    "solicitationNumber": "EDU-2026-001",
                    "fullParentPathName": "DEPT OF EDUCATION.OFFICE OF ELEMENTARY PROGRAMS",
                    "postedDate": "2026-06-01",
                    "responseDeadLine": "2026-06-30T17:00:00-04:00",
                    "uiLink": "https://sam.gov/workspace/contract/opp/abc123/view",
                    "description": "Support evidence-based reading intervention services.",
                    "naicsCode": "611710",
                }
            ],
        }
        return httpx.Response(200, json=payload)

    _mock_http(monkeypatch, handler)
    _, impl = _factory(_ctx())
    items = json.loads(
        await impl(
            {
                "keyword": "education",
                "limit": 1,
                "lookbackDays": 10,
                "ptype": "o",
            }
        )
    )

    assert seen["keyword"] == "education"
    assert seen["limit"] == "1"
    assert seen["ptype"] == "o"
    assert seen["ncode"] == "611110,611710,611310,611691,624310"
    assert seen["postedFrom"].count("/") == 2
    assert seen["postedTo"].count("/") == 2
    assert items == [
        {
            "title": "K-12 Literacy Tutoring Services",
            "solicitation_number": "EDU-2026-001",
            "agency": "DEPT OF EDUCATION.OFFICE OF ELEMENTARY PROGRAMS",
            "posted_date": "2026-06-01",
            "response_deadline": "2026-06-30T17:00:00-04:00",
            "url": "https://sam.gov/workspace/contract/opp/abc123/view",
            "description": "Support evidence-based reading intervention services.",
            "naics": "611710",
        }
    ]


@pytest.mark.asyncio
async def test_procurement_uses_title_and_custom_naics_when_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAM_API_KEY", "test-key")
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["keyword"] = request.url.params.get("keyword", "")
        seen["title"] = request.url.params.get("title", "")
        seen["ncode"] = request.url.params.get("ncode", "")
        payload = {
            "totalRecords": 1,
            "opportunitiesData": [
                {
                    "title": "K-12 Reading Assessment Platform",
                    "solicitationNumber": "EDU-2026-002",
                    "fullParentPathName": "DEPT OF EDUCATION.TESTING",
                    "postedDate": "2026-06-01",
                    "responseDeadLine": "2026-06-30T17:00:00-04:00",
                    "uiLink": "https://sam.gov/workspace/contract/opp/def456/view",
                    "description": "Assessment tools for school districts.",
                    "naicsCode": "611110",
                }
            ],
        }
        return httpx.Response(200, json=payload)

    _mock_http(monkeypatch, handler)
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"title": "reading assessment", "naics": ["611110", "611710"]}))

    assert seen["keyword"] == ""
    assert seen["title"] == "reading assessment"
    assert seen["ncode"] == "611110,611710"
    assert items[0]["solicitation_number"] == "EDU-2026-002"


@pytest.mark.asyncio
async def test_procurement_filters_non_education_false_positives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAM_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        payload = {
            "totalRecords": 2,
            "opportunitiesData": [
                {
                    "title": "Literature Storage Rack Replacement Part",
                    "solicitationNumber": "DOD-001",
                    "fullParentPathName": "DEPT OF DEFENSE.DEFENSE LOGISTICS AGENCY",
                    "postedDate": "2026-06-01",
                    "responseDeadLine": "2026-06-15T17:00:00-04:00",
                    "uiLink": "https://sam.gov/workspace/contract/opp/dod001/view",
                    "description": "Replacement part for warehouse equipment.",
                    "naicsCode": "332510",
                },
                {
                    "title": "K-12 Literacy Tutoring Support",
                    "solicitationNumber": "EDU-003",
                    "fullParentPathName": "DEPT OF EDUCATION.OFFICE OF STUDENT SUPPORT",
                    "postedDate": "2026-06-01",
                    "responseDeadLine": "2026-06-20T17:00:00-04:00",
                    "uiLink": "https://sam.gov/workspace/contract/opp/edu003/view",
                    "description": "Tutoring and intervention support for schools.",
                    "naicsCode": "611710",
                },
            ],
        }
        return httpx.Response(200, json=payload)

    _mock_http(monkeypatch, handler)
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"keyword": "literacy"}))

    assert [item["solicitation_number"] for item in items] == ["EDU-003"]


@pytest.mark.asyncio
async def test_procurement_non_200_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAM_API_KEY", "test-key")
    _mock_http(monkeypatch, lambda request: httpx.Response(418, content=b"teapot"))
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"query": "literacy"})) == []


@pytest.mark.asyncio
async def test_procurement_api_error_or_bad_json_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAM_API_KEY", "test-key")

    _mock_http(
        monkeypatch,
        lambda request: httpx.Response(
            200,
            json={"errorCode": "400", "errorMessage": "Invalid Date Entered."},
        ),
    )
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"query": "literacy"})) == []

    _mock_http(monkeypatch, lambda request: httpx.Response(200, content=b"not-json"))
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"query": "literacy"})) == []


@pytest.mark.asyncio
async def test_procurement_empty_opportunities_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAM_API_KEY", "test-key")
    _mock_http(
        monkeypatch,
        lambda request: httpx.Response(200, json={"totalRecords": 0, "opportunitiesData": []}),
    )
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"keywords": ["reading", "assessment"]})) == []
