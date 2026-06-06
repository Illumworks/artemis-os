"""Tests for cost_event recording in artemis.builders.executor.

Verifies that run_agent writes a cost_events row on success and on failure
(lossless recording invariant).

Requires ARTEMIS_TEST_DB_URL pointing at a DB that has both the builders tables
AND the cost_events table (i.e., migrated to 0066+).
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.costs.models  # noqa: F401 — register CostEvent on Base.metadata
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent
from artemis.costs.models import CostEvent


def _agent_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "cost-test-agent",
        "name": "Cost Test Agent",
        "goal": "Test cost recording",
        "system_prompt": "Test.",
        "tools": [],
        "model": "claude-sonnet-4-6",
        "provider": "anthropic",
    }
    base.update(overrides)
    return base


def _fake(text: str = "done", input_tokens: int = 100, output_tokens: int = 50) -> FakeAdapter:
    return FakeAdapter(
        [ScriptedReply(text=text, input_tokens=input_tokens, output_tokens=output_tokens)]
    )


@pytest.mark.asyncio
async def test_successful_run_writes_cost_event(db_session: AsyncSession) -> None:
    """A successful agent run produces both an agent_runs row AND a cost_events row."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs())

    run = await run_agent(
        session=db_session,
        agent_id="cost-test-agent",
        model_adapter=_fake(input_tokens=200, output_tokens=100),
    )
    await db_session.commit()

    assert run.status == "completed"

    # Verify a cost_events row was written
    result = await db_session.execute(select(CostEvent).where(CostEvent.feature_tag == "agent_run"))
    events = result.scalars().all()
    assert len(events) >= 1

    event = events[-1]  # latest
    assert event.input_tokens == 200
    assert event.output_tokens == 100
    assert event.provider == "anthropic"
    assert event.feature_tag == "agent_run"
    assert event.source_kind == "agent_run"
    assert event.is_error is False
    assert event.cost_usd > 0.0


@pytest.mark.asyncio
async def test_failed_run_writes_error_cost_event(db_session: AsyncSession) -> None:
    """An errored agent run still writes a cost_events row with is_error=True."""

    class BoomAdapter:
        """Adapter that raises immediately."""

        async def complete(self, request: Any) -> Any:
            raise RuntimeError("Simulated LLM failure")

        async def run_with_tools(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Simulated LLM failure")

    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="boom-agent"))

    run = await run_agent(
        session=db_session,
        agent_id="boom-agent",
        model_adapter=BoomAdapter(),
    )
    await db_session.commit()

    assert run.status == "failed"

    # An error cost_events row must exist (lossless recording)
    result = await db_session.execute(
        select(CostEvent).where(
            CostEvent.feature_tag == "agent_run",
            CostEvent.is_error.is_(True),
        )
    )
    events = result.scalars().all()
    assert len(events) >= 1
    assert events[-1].error_kind is not None
