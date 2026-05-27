"""Tests for state_doe.fetch tool — uses MockTransport, no live HTTP."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from artemis.tools.context import ToolContext
from artemis.tools.state_doe import _factory

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


class _FakeSession:
    pass


def _ctx() -> ToolContext:
    return ToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        agent_id="marketing.scout.state_doe",
        agent_db_id=1,
        agent_run_id="run-test",
        pipeline_run_id=None,
    )


def _load_fixture(name: str) -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fetch_with_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = _load_fixture("state_doe_sample.xml")
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
    items = json.loads(await impl({"state": "FL"}))
    assert len(items) >= 1 and items[0]["title"] and items[0]["link"]


@pytest.mark.asyncio
async def test_fetch_unknown_state() -> None:
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"state": "ZZ"})) == []


@pytest.mark.asyncio
async def test_fetch_empty_state() -> None:
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"state": ""})) == []


@pytest.mark.asyncio
async def test_fetch_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from artemis.scouts._http import ScoutHttpClient

    orig = ScoutHttpClient.__init__

    def patched(self: ScoutHttpClient, **kwargs: Any) -> None:
        kwargs["_inner"] = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500, content=b"err"))
        )
        orig(self, **kwargs)

    monkeypatch.setattr(ScoutHttpClient, "__init__", patched)
    _, impl = _factory(_ctx())
    assert json.loads(await impl({"state": "FL"})) == []
