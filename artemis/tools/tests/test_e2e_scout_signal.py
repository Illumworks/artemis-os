"""P2 — End-to-end: scout LLM emits tool_use → signal_queue row created."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent
from artemis.marketing.josh_spec import parse_spec, reason_codes_for_scout
from artemis.marketing.models import SignalQueue

_AGENT_ID = "marketing.scout.regional_news"
_SLUG = "regional_news"


def _valid_tool_input() -> dict[str, Any]:
    spec = parse_spec()
    codes = reason_codes_for_scout(spec, _SLUG)
    first_code = codes[0].code if codes else "VENDOR_DISSATISFACTION"
    return {
        "sourceType": "news_article",
        "headline": "District evaluates new reading curriculum",
        "campaignFamily": "obc",
        "urgencyTier": "standard",
        "reasonCodes": [first_code],
        "evidence": "Board announced publicly.",
        "sourceUrl": "https://example-news.com/district",
    }


@pytest.mark.asyncio
async def test_e2e_scout_emits_signal(db_session: AsyncSession) -> None:
    """Full loop: seed agent → mock LLM tool_use → signal_queue row created."""
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            agent_id=_AGENT_ID,
            name="Regional News Scout",
            goal="Find OBC signals.",
            system_prompt="You are a regional news scout.",
            tools=["signal_queue.write"],
            model="claude-sonnet-4-6",
        )

    adapter = FakeAdapter(
        [
            ScriptedReply(
                tool_calls=[("tu-e2e-1", "signal_queue.write", _valid_tool_input())],
                stop_reason="tool_use",
            ),
            ScriptedReply(text="Signal written.", stop_reason="end_turn"),
        ]
    )

    run = await run_agent(
        session=db_session,
        agent_id=_AGENT_ID,
        model_adapter=adapter,
    )
    await db_session.commit()

    assert run.status == "completed", f"run failed: {getattr(run, 'error', '?')}"

    rows = (await db_session.execute(select(SignalQueue))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.discovered_by == _SLUG
    assert row.signal_status == "pending_qualification"
    assert row.pipeline_run_id is None
    assert row.provenance is not None
    assert row.provenance["agent_id"] == _AGENT_ID
    assert row.provenance["agent_run_id"] == run.run_id
