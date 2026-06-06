"""Tests for the scout qualification gap fix.

Verifies:
  1. signal_queue.write tool → qualification_json populated + status not pending_qualification
  2. scout_runner → same effect
  3. No active ruleset → signal still created, qualification skipped gracefully (non-fatal)
  4. intake/qualify route behaviour is unchanged (regression guard)
  5. run_and_store_qualification directly
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401 — register builders models for TRUNCATE safety
from artemis.marketing.models import Ruleset, SignalQueue, TerritoryConfig
from artemis.marketing.qualification import run_and_store_qualification
from artemis.marketing.repository import create_signal
from artemis.marketing.scout_runner import ScoutMode, run_scout
from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter
from artemis.marketing.seeds.reason_codes import seed_reason_codes
from artemis.tools.context import ToolContext
from artemis.tools.signal_queue import _factory

pytestmark = pytest.mark.asyncio

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

_SCOUT_AGENT_ID = "marketing.scout.regional_news"
_SCOUT_SLUG = "regional_news"
_SCOUT_RUNNER_ID = "marketing.scout.starbridge_researcher"


async def _seed_ruleset(session: AsyncSession, family: str = "obc") -> Ruleset:
    """Insert one active ruleset with a weighted signal for DISTRICT_VOTED_YES."""
    rs = Ruleset(
        family=family,
        version_tag="v1",
        state="active",
        hard_filters=[],
        weighted_signals=[
            {"rule_id": "r1", "reason_code": "DISTRICT_VOTED_YES", "weight": 0.7}
        ],
        qualitative_rubrics=[],
    )
    session.add(rs)
    await session.flush()
    await session.refresh(rs)
    return rs


async def _seed_territory(session: AsyncSession, family: str = "obc") -> TerritoryConfig:
    tc = TerritoryConfig(
        family=family,
        hot_states=["CA", "TX"],
        standard_states=["NY", "FL"],
        unlisted_multiplier=0.85,
    )
    session.add(tc)
    await session.flush()
    await session.refresh(tc)
    return tc


def _tool_ctx(session: AsyncSession) -> ToolContext:
    return ToolContext(
        session=session,
        agent_id=_SCOUT_AGENT_ID,
        agent_db_id=1,
        agent_run_id="run-test-qualfix",
        pipeline_run_id=None,
    )


def _valid_tool_payload(**overrides: Any) -> dict[str, Any]:
    from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout

    spec = parse_spec()
    codes = reason_codes_for_scout(spec, _SCOUT_SLUG)
    first_code = codes[0].code if codes else "VENDOR_DISSATISFACTION"
    base: dict[str, Any] = {
        "sourceType": "news_article",
        "headline": "District adopts new literacy program",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [first_code],
        "evidence": "Announced in board minutes.",
        "sourceUrl": "https://example.com/qualfix-test",
        "stateCode": "CA",
    }
    base.update(overrides)
    return base


def _llm_response(payload: dict[str, Any]) -> AsyncMock:
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    resp = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=json.dumps(payload))]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=100, output_tokens=50),
    )
    return AsyncMock(return_value=resp)


_SCOUT_PAYLOAD = {
    "headline": "District adopts literacy program",
    "sourceType": "starbridge",
    "sourceUrl": "https://example.com/scout-qualfix",
    "campaignFamily": "obc",
    "urgencyTier": "standard",
    "reasonCodes": [],
    "whyFlagged": "r",
    "evidence": "e",
}


class _MockAdapter(ScoutSourceAdapter):
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self, tc: Any, lr: Any) -> list[RawItem]:
        return list(self._items)


# ─────────────────────────────────────────────────────────────────────────────
# 1. signal_queue.write tool: with active ruleset → signal qualifies
# ─────────────────────────────────────────────────────────────────────────────


async def test_tool_write_qualifies_signal_with_active_ruleset(
    db_session: AsyncSession,
) -> None:
    """signal_queue.write: with an active ruleset + territory, signal is qualified after write."""
    await seed_reason_codes(db_session)
    await _seed_ruleset(db_session)
    await _seed_territory(db_session)

    ctx = _tool_ctx(db_session)
    _, impl = _factory(ctx)
    raw = await impl(_valid_tool_payload())
    data = json.loads(raw)
    assert data["status"] == "written"

    row = await db_session.get(SignalQueue, data["signal_id"])
    assert row is not None, "signal row must exist"
    # Key assertions: qualification_json must be populated and status advanced
    assert row.qualification_json is not None, "qualification_json must be set"
    assert "qualifiedAt" in row.qualification_json, "qual dict must have qualifiedAt key"
    assert row.signal_status != "pending_qualification", (
        f"status must not be pending_qualification after qualification; got {row.signal_status!r}"
    )
    assert row.signal_status == "qualified", (
        f"expected 'qualified', got {row.signal_status!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 2. signal_queue.write tool: no active ruleset → signal written, no crash
# ─────────────────────────────────────────────────────────────────────────────


async def test_tool_write_no_ruleset_signal_created_gracefully(
    db_session: AsyncSession,
) -> None:
    """signal_queue.write: no active rulesets → signal still created, status stays pending."""
    await seed_reason_codes(db_session)
    # Deliberately do NOT seed a ruleset

    ctx = _tool_ctx(db_session)
    _, impl = _factory(ctx)
    raw = await impl(_valid_tool_payload(sourceUrl="https://example.com/no-ruleset"))
    data = json.loads(raw)
    assert data["status"] == "written", f"expected written, got {data}"

    row = await db_session.get(SignalQueue, data["signal_id"])
    assert row is not None, "signal row must exist"
    # No ruleset → qualification skipped gracefully; signal stays pending
    assert row.signal_status == "pending_qualification", (
        f"expected pending_qualification when no ruleset, got {row.signal_status!r}"
    )
    assert row.qualification_json is None, "qualification_json should be None when no ruleset"


# ─────────────────────────────────────────────────────────────────────────────
# 3. scout_runner: with active ruleset → signal qualifies
# ─────────────────────────────────────────────────────────────────────────────


async def test_scout_runner_qualifies_signal_with_active_ruleset(
    db_session: AsyncSession,
) -> None:
    """run_scout: emitted signals are qualified when an active ruleset exists."""
    from sqlalchemy import text

    _truncate = text(
        "TRUNCATE agent_context, agent_run_trajectory_summaries, definition_proposals, "
        "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
    )
    await db_session.execute(_truncate)
    await db_session.commit()

    from artemis.marketing.seeds.marketing_agents import seed_marketing_agents

    await seed_marketing_agents(db_session)

    await _seed_ruleset(db_session, family="obc")
    await _seed_territory(db_session, family="obc")
    await db_session.commit()

    items = [RawItem(content="c1", source_url="https://ex.com/scout-q1")]
    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=_llm_response(_SCOUT_PAYLOAD)),
    ):
        r = await run_scout(
            db_session,
            _SCOUT_RUNNER_ID,
            ScoutMode.manual,
            adapter_override=_MockAdapter(items),
        )
    await db_session.commit()

    assert r.signals_emitted == 1, f"expected 1 signal emitted, got {r.signals_emitted}"

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.qualification_json is not None, "qualification_json must be set after scout run"
    assert "qualifiedAt" in row.qualification_json
    assert row.signal_status != "pending_qualification", (
        f"status must not be pending_qualification; got {row.signal_status!r}"
    )
    assert row.signal_status == "qualified"


# ─────────────────────────────────────────────────────────────────────────────
# 4. scout_runner: no active ruleset → signal created, no crash
# ─────────────────────────────────────────────────────────────────────────────


async def test_scout_runner_no_ruleset_signal_created_gracefully(
    db_session: AsyncSession,
) -> None:
    """run_scout: with no active rulesets, signals are still emitted (non-fatal)."""
    from sqlalchemy import text

    _truncate = text(
        "TRUNCATE agent_context, agent_run_trajectory_summaries, definition_proposals, "
        "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
    )
    await db_session.execute(_truncate)
    await db_session.commit()

    from artemis.marketing.seeds.marketing_agents import seed_marketing_agents

    await seed_marketing_agents(db_session)
    # Deliberately do NOT seed a ruleset

    items = [RawItem(content="c2", source_url="https://ex.com/scout-noruleset")]
    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=_llm_response(_SCOUT_PAYLOAD)),
    ):
        r = await run_scout(
            db_session,
            _SCOUT_RUNNER_ID,
            ScoutMode.manual,
            adapter_override=_MockAdapter(items),
        )
    await db_session.commit()

    assert r.signals_emitted == 1
    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.signal_status == "pending_qualification", (
        f"no ruleset → must stay pending, got {row.signal_status!r}"
    )
    assert row.qualification_json is None


# ─────────────────────────────────────────────────────────────────────────────
# 5. run_and_store_qualification directly — with active ruleset
# ─────────────────────────────────────────────────────────────────────────────


async def test_run_and_store_qualification_populates_json(
    db_session: AsyncSession,
) -> None:
    """run_and_store_qualification returns qual dict and writes to DB."""
    await _seed_ruleset(db_session)
    await _seed_territory(db_session)
    signal = await create_signal(
        db_session,
        headline="Board voted yes",
        campaign_family="obc",
        source_type="manual",
        summary="x",
        discovered_by="manual",
        reason_codes=[{"code": "DISTRICT_VOTED_YES", "confidence": 1.0}],
        state="CA",
    )
    await db_session.flush()

    result = await run_and_store_qualification(db_session, signal)
    await db_session.commit()
    await db_session.refresh(signal)

    assert result is not None, "result must not be None when active rulesets exist"
    assert "qualifiedAt" in result
    assert "scores" in result
    assert signal.qualification_json == result
    assert signal.signal_status == "qualified"


# ─────────────────────────────────────────────────────────────────────────────
# 6. run_and_store_qualification — no active ruleset → returns None (non-fatal)
# ─────────────────────────────────────────────────────────────────────────────


async def test_run_and_store_qualification_returns_none_when_no_ruleset(
    db_session: AsyncSession,
) -> None:
    """run_and_store_qualification returns None (no crash) when no active rulesets."""
    signal = await create_signal(
        db_session,
        headline="Some headline",
        campaign_family="obc",
        source_type="manual",
        summary="x",
        discovered_by="manual",
        reason_codes=[],
        state="TX",
    )
    await db_session.flush()

    result = await run_and_store_qualification(db_session, signal)
    await db_session.commit()
    await db_session.refresh(signal)

    assert result is None, "must return None when no active rulesets"
    assert signal.signal_status == "pending_qualification", (
        "status must remain pending_qualification when no ruleset"
    )
    assert signal.qualification_json is None
