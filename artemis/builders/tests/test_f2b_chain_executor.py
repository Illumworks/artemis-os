"""Tests for artemis.builders.chain_executor (F2b — chain execution wiring).

Verifies that each step's output is passed as the next step's input.
Uses FakeAdapter to avoid real Anthropic API calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.chain_executor import run_chain

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake(*texts: str) -> FakeAdapter:
    return FakeAdapter([ScriptedReply(text=t) for t in texts])


async def _create_agents(session: AsyncSession, *agent_ids: str) -> None:
    async with session.begin():
        for aid in agent_ids:
            await repo.create_agent(
                session,
                agent_id=aid,
                name=f"Agent {aid}",
                goal=f"Do {aid}",
                model="claude-sonnet-4-6",
                tools=[],
            )


async def _create_chain(session: AsyncSession, chain_id: str, steps: list[Any]) -> None:
    async with session.begin():
        await repo.create_agent_chain(session, chain_id=chain_id, name=chain_id, steps=steps)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chain_returns_ordered_runs(db_session: AsyncSession) -> None:
    """run_chain returns one AgentRun per step in order."""
    await _create_agents(db_session, "chain-a1", "chain-a2")
    await _create_chain(
        db_session,
        "ch-order",
        [{"agent_id": "chain-a1"}, {"agent_id": "chain-a2"}],
    )

    runs = await run_chain(
        session=db_session,
        chain_id="ch-order",
        initial_message="start",
        model_adapter=fake("output-from-a1", "output-from-a2"),
    )
    await db_session.commit()

    assert len(runs) == 2
    assert runs[0].agent_id == "chain-a1"
    assert runs[1].agent_id == "chain-a2"
    assert all(r.status == "completed" for r in runs)


@pytest.mark.asyncio
async def test_chain_passes_output_to_next_step(db_session: AsyncSession) -> None:
    """Each step's final_response is passed as user_message to the next step."""
    await _create_agents(db_session, "chain-b1", "chain-b2")
    await _create_chain(
        db_session,
        "ch-pass",
        [{"agent_id": "chain-b1"}, {"agent_id": "chain-b2"}],
    )

    adapter = FakeAdapter(
        [
            ScriptedReply(text="step1 result"),
            ScriptedReply(text="step2 done"),
        ]
    )

    await run_chain(
        session=db_session,
        chain_id="ch-pass",
        initial_message="initial seed",
        model_adapter=adapter,
    )
    await db_session.commit()

    # Step 2's model call should have received step 1's output as the user message
    assert len(adapter.requests) == 2
    step2_msg = adapter.requests[1].messages[0].content
    text = "".join(b.text for b in step2_msg if hasattr(b, "text"))
    assert "step1 result" in text


@pytest.mark.asyncio
async def test_chain_initial_message_goes_to_first_step(db_session: AsyncSession) -> None:
    """The initial_message is passed verbatim to the first agent."""
    await _create_agents(db_session, "chain-c1")
    await _create_chain(db_session, "ch-init", [{"agent_id": "chain-c1"}])

    adapter = fake("done")
    await run_chain(
        session=db_session,
        chain_id="ch-init",
        initial_message="Hello first agent",
        model_adapter=adapter,
    )
    await db_session.commit()

    first_msg = adapter.requests[0].messages[0].content
    text = "".join(b.text for b in first_msg if hasattr(b, "text"))
    assert "Hello first agent" in text


@pytest.mark.asyncio
async def test_chain_fails_fast_on_step_failure(db_session: AsyncSession) -> None:
    """Default on_failure='fail' stops the chain if an agent run fails."""
    await _create_agents(db_session, "chain-d1", "chain-d2")
    await _create_chain(
        db_session,
        "ch-fail",
        [{"agent_id": "chain-d1"}, {"agent_id": "chain-d2"}],
    )

    class BrokenAdapter:
        async def complete(self, _request: Any) -> Any:
            raise RuntimeError("broken")

    runs = await run_chain(
        session=db_session,
        chain_id="ch-fail",
        initial_message="start",
        model_adapter=BrokenAdapter(),
    )
    await db_session.commit()

    # Only the first run should be recorded; chain aborted after failure
    assert len(runs) == 1
    assert runs[0].status == "failed"


@pytest.mark.asyncio
async def test_empty_chain_returns_empty_list(db_session: AsyncSession) -> None:
    """An empty steps list returns an empty list immediately."""
    await _create_chain(db_session, "ch-empty", [])

    runs = await run_chain(
        session=db_session,
        chain_id="ch-empty",
        model_adapter=fake(),
    )
    await db_session.commit()

    assert runs == []


@pytest.mark.asyncio
async def test_chain_not_found_raises(db_session: AsyncSession) -> None:
    """run_chain raises ValueError for an unknown chain_id."""
    with pytest.raises(ValueError, match="not found"):
        await run_chain(
            session=db_session,
            chain_id="ghost-chain",
            model_adapter=fake("nope"),
        )
