"""Tests for reason_codes tools."""

from __future__ import annotations

import json

import pytest

from artemis.marketing.josh_spec import parse_spec
from artemis.tools.context import ToolContext
from artemis.tools.reason_codes import _factory_allowlist, _factory_lookup


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


@pytest.mark.asyncio
async def test_get_allowlist_returns_codes() -> None:
    _, impl = _factory_allowlist(_ctx())
    codes = json.loads(await impl({}))
    assert isinstance(codes, list)
    assert all("code" in c and "description" in c and "default_urgency" in c for c in codes)


@pytest.mark.asyncio
async def test_lookup_known_code() -> None:
    first = parse_spec().reason_codes[0]
    _, impl = _factory_lookup(_ctx())
    data = json.loads(await impl({"code": first.code}))
    assert data["code"] == first.code
    assert "what_scout_looks_for" in data


@pytest.mark.asyncio
async def test_lookup_unknown_code() -> None:
    _, impl = _factory_lookup(_ctx())
    result = await impl({"code": "TOTALLY_FAKE_CODE_XYZ"})
    assert result.startswith("ERROR") and "TOTALLY_FAKE_CODE_XYZ" in result


@pytest.mark.asyncio
async def test_lookup_missing_arg() -> None:
    _, impl = _factory_lookup(_ctx())
    assert (await impl({})).startswith("ERROR")
