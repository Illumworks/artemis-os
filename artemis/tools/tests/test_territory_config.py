"""Tests for territory_config tools."""

from __future__ import annotations

import json

import pytest

from artemis.marketing.josh_spec import parse_spec
from artemis.tools.context import ToolContext
from artemis.tools.territory_config import _factory_priority_states, _factory_watch_keywords


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


@pytest.mark.asyncio
async def test_get_priority_states() -> None:
    _, impl = _factory_priority_states(_ctx())
    states = json.loads(await impl({}))
    assert isinstance(states, list) and len(states) > 0
    assert set(states) == set(parse_spec().territory_config.priority_states)


@pytest.mark.asyncio
async def test_get_watch_keywords_all() -> None:
    _, impl = _factory_watch_keywords(_ctx())
    data = json.loads(await impl({}))
    assert isinstance(data, dict) and len(data) > 0


@pytest.mark.asyncio
async def test_get_watch_keywords_filtered() -> None:
    first_type = parse_spec().campaign_type_mappings[0].campaign_type
    _, impl = _factory_watch_keywords(_ctx())
    data = json.loads(await impl({"campaignType": first_type}))
    assert list(data.keys()) == [first_type]


@pytest.mark.asyncio
async def test_get_watch_keywords_unknown() -> None:
    _, impl = _factory_watch_keywords(_ctx())
    assert json.loads(await impl({"campaignType": "NONEXISTENT_XYZ"})) == {}
