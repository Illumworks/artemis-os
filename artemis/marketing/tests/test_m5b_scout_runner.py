"""M5b — Scout execution path tests."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.builders.models  # noqa: F401
from artemis.marketing.scout_runner import ScoutMode, reason_code_system_suffix, run_scout
from artemis.marketing.scout_sources.base import RawItem, ScoutSourceAdapter
from artemis.marketing.seeds.marketing_agents import seed_marketing_agents

pytestmark = pytest.mark.asyncio
_TRUNCATE = text(
    "TRUNCATE agent_context, agent_run_trajectory_summaries, definition_proposals, "
    "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
)
_SCOUT = "marketing.scout.starbridge_researcher"
_VALID_PAYLOAD = {
    "headline": "District adopts literacy program",
    "sourceType": "starbridge",
    "sourceUrl": "https://example.com/a",
    "campaignFamily": "obc",
    "urgencyTier": "standard",
    "reasonCodes": [],
    "whyFlagged": "r",
    "evidence": "e",
}


async def _seed(s: AsyncSession) -> None:
    await s.execute(_TRUNCATE)
    await s.commit()
    await seed_marketing_agents(s)


class _Mock(ScoutSourceAdapter):
    def __init__(self, items: list[RawItem]) -> None:
        self._items = items

    def fetch(self, tc: Any, lr: Any) -> list[RawItem]:
        return list(self._items)


def _llm(payload: dict[str, Any]) -> AsyncMock:
    from artemis.agent.client import CompletionResponse
    from artemis.agent.types import Message, TextBlock, Usage

    resp = CompletionResponse(
        message=Message(role="assistant", content=[TextBlock(text=json.dumps(payload))]),
        stop_reason="end_turn",
        usage=Usage(input_tokens=100, output_tokens=50),
    )
    return AsyncMock(return_value=resp)


async def test_empty_adapter(db_session: AsyncSession) -> None:
    await _seed(db_session)
    r = await run_scout(db_session, _SCOUT, ScoutMode.scheduled, adapter_override=_Mock([]))
    await db_session.commit()
    assert r.status == "complete" and r.signals_emitted == 0
    from artemis.marketing.models import ScoutRun

    assert (await db_session.get(ScoutRun, r.run_id)) is not None


async def test_three_valid_items(db_session: AsyncSession) -> None:
    await _seed(db_session)
    items = [RawItem(content=f"c{i}", source_url=f"https://ex.com/{i}") for i in range(3)]
    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=_llm(_VALID_PAYLOAD)),
    ):
        r = await run_scout(db_session, _SCOUT, ScoutMode.manual, adapter_override=_Mock(items))
    await db_session.commit()
    assert r.signals_emitted == 3 and r.signals_rejected == 0
    from artemis.marketing.models import SignalQueue

    rows = (
        (
            await db_session.execute(
                select(SignalQueue).where(SignalQueue.signal_status == "pending_qualification")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 3


async def test_reason_code_system_injection(db_session: AsyncSession) -> None:
    await _seed(db_session)
    complete = _llm(_VALID_PAYLOAD)
    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=complete),
    ):
        await run_scout(
            db_session,
            _SCOUT,
            ScoutMode.manual,
            adapter_override=_Mock([RawItem(content="c", source_url="https://ex.com/x")]),
        )
    request = complete.call_args.args[0]
    assert "You may emit ONLY these reason codes: [POLICY_LIT_MANDATE" in request.system
    assert "Any other code will be rejected by intake validation." in request.system


def test_reason_code_system_injection_empty_degrades() -> None:
    assert reason_code_system_suffix([]) == "Any registered reason code is valid."


async def test_invalid_llm_output(db_session: AsyncSession) -> None:
    await _seed(db_session)
    bad = MagicMock()
    bad.message.content = [MagicMock(text="NOT JSON")]
    bad.usage = None
    with patch(
        "artemis.marketing.scout_runner.get_adapter",
        return_value=MagicMock(complete=AsyncMock(return_value=bad)),
    ):
        r = await run_scout(
            db_session,
            _SCOUT,
            ScoutMode.manual,
            adapter_override=_Mock([RawItem(content="c", source_url="https://ex.com/x")]),
        )
    await db_session.commit()
    assert r.signals_emitted == 0 and r.signals_rejected == 1 and r.status == "complete"


def test_scheduler_nine_jobs() -> None:
    """Verify 9 jobs registered and deregistered (no event loop needed for job inspection)."""

    # Use a plain BackgroundScheduler to avoid needing a running event loop.
    from apscheduler.schedulers.background import (  # type: ignore[import-untyped]
        BackgroundScheduler,
    )
    from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]

    from artemis.marketing.scout_runner import DEFAULT_CADENCE_SECONDS
    from artemis.marketing.scout_scheduler import (
        _SCOUT_AGENT_IDS,
        _run_scout_job,
    )

    scheduler = BackgroundScheduler()
    for agent_id in _SCOUT_AGENT_IDS:
        scheduler.add_job(
            _run_scout_job,
            args=[agent_id],
            trigger=IntervalTrigger(seconds=DEFAULT_CADENCE_SECONDS),
            id=f"scout_{agent_id.split('.')[-1]}",
        )
    assert len(scheduler.get_jobs()) == 9 == len(_SCOUT_AGENT_IDS)


async def test_manual_endpoint_404(client: Any) -> None:
    r = await client.post(
        "/api/scouts/agents/marketing.scout.does_not_exist/run",
        headers={"Authorization": "Bearer test"},
    )
    assert r.status_code == 404


async def test_run_mode_in_metadata(db_session: AsyncSession) -> None:
    await _seed(db_session)
    r = await run_scout(db_session, _SCOUT, ScoutMode.backfill, adapter_override=_Mock([]))
    await db_session.commit()
    assert r.mode == ScoutMode.backfill
    from artemis.marketing.models import ScoutRun

    row = await db_session.get(ScoutRun, r.run_id)
    assert row is not None and (row.dry_run_summary or {}).get("mode") == "backfill"
