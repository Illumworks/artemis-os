"""Tests for news_api.search tool — uses MockTransport, no live HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from artemis.tools.context import ToolContext
from artemis.tools.news import _factory, _parse_google_news_rss

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


class _FakeSession:
    pass


def _ctx() -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id="marketing.scout.regional_news",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _load_fixture(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


def test_parse_rss_fixture() -> None:
    items = _parse_google_news_rss(_load_fixture("google_news_sample.xml"))
    assert len(items) >= 1
    assert all("title" in i and "link" in i for i in items)


def test_parse_rss_malformed() -> None:
    assert _parse_google_news_rss("<not valid xml><<") == []


def test_parse_rss_empty() -> None:
    assert _parse_google_news_rss("") == []


@pytest.mark.asyncio
async def test_search_with_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = _load_fixture("google_news_sample.xml")
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, content=xml.encode("utf-8"))
            )
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _factory(_ctx())
    items = json.loads(await impl({"query": "Florida schools literacy"}))
    assert len(items) >= 1 and items[0]["title"]


@pytest.mark.asyncio
async def test_search_empty_query() -> None:
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"query": ""})) == []


@pytest.mark.asyncio
async def test_search_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500, content=b"err"))
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"query": "Florida"})) == []
