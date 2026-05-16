"""Tests for artemis.builders.executor (F2b — agent execution wiring).

Uses FakeAdapter to avoid real Anthropic API calls.
Requires a running Postgres at ARTEMIS_TEST_DB_URL, migrated to head.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "test-agent-exec",
        "name": "Exec Agent",
        "goal": "Test goal",
        "system_prompt": "You are a tester.",
        "tools": [],
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


def fake(text: str = "Hello from fake", **kwargs: Any) -> FakeAdapter:
    return FakeAdapter([ScriptedReply(text=text, **kwargs)])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_returns_completed_run(db_session: AsyncSession) -> None:
    """run_agent with a simple agent definition returns status='completed'."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs())

    run = await run_agent(
        session=db_session,
        agent_id="test-agent-exec",
        model_adapter=fake("All done!"),
    )
    await db_session.commit()

    assert run.status == "completed"
    assert run.run_id is not None
    assert run.agent_id == "test-agent-exec"


@pytest.mark.asyncio
async def test_final_response_stored_in_context(db_session: AsyncSession) -> None:
    """The assistant's final text is stored in agent_context under 'final_response'."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs())

    run = await run_agent(
        session=db_session,
        agent_id="test-agent-exec",
        model_adapter=fake("I am the response."),
    )
    await db_session.commit()

    ctx = await repo.get_agent_context(db_session, run.run_id, "final_response")
    assert "I am the response." in ctx.value


@pytest.mark.asyncio
async def test_shared_context_injected_into_system_prompt(db_session: AsyncSession) -> None:
    """shared_context appears as ## Context block in the system prompt sent to the model."""
    adapter = fake("ok")

    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs(agent_id="ctx-agent"))

    await run_agent(
        session=db_session,
        agent_id="ctx-agent",
        shared_context={"project": "artemis", "env": "test"},
        model_adapter=adapter,
    )
    await db_session.commit()

    # The adapter recorded the request; check the system prompt contains the context
    assert len(adapter.requests) == 1
    system = adapter.requests[0].system or ""
    assert "## Context" in system
    assert "artemis" in system


@pytest.mark.asyncio
async def test_empty_tools_list_runs_without_tools(db_session: AsyncSession) -> None:
    """Agents with empty tools list run cleanly (tools=None passed to run_turn)."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs(agent_id="notool-agent", tools=[]))

    run = await run_agent(
        session=db_session,
        agent_id="notool-agent",
        model_adapter=fake("no tools needed"),
    )
    await db_session.commit()

    assert run.status == "completed"


@pytest.mark.asyncio
async def test_nonempty_tools_warns_but_still_runs(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-empty tools JSONB triggers a warning and still completes (V1 stub)."""
    import logging

    async with db_session.begin():
        await repo.create_agent(
            db_session,
            **_make_agent_kwargs(agent_id="tool-agent", tools=["bash", "read"]),
        )

    with caplog.at_level(logging.WARNING, logger="artemis.builders.executor"):
        run = await run_agent(
            session=db_session,
            agent_id="tool-agent",
            model_adapter=fake("done"),
        )
    await db_session.commit()

    assert run.status == "completed"
    assert any("tools" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_exception_sets_status_failed(db_session: AsyncSession) -> None:
    """If the adapter raises, the run status is set to 'failed' with an error message."""

    class BrokenAdapter:
        async def complete(self, _request: Any) -> Any:
            raise RuntimeError("Adapter exploded")

    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs(agent_id="fail-agent"))

    run = await run_agent(
        session=db_session,
        agent_id="fail-agent",
        model_adapter=BrokenAdapter(),
    )
    await db_session.commit()

    assert run.status == "failed"
    assert run.error is not None
    assert "RuntimeError" in run.error


@pytest.mark.asyncio
async def test_token_cost_recorded_on_success(db_session: AsyncSession) -> None:
    """Completed runs have non-zero token counts from the fake adapter."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs(agent_id="cost-agent"))

    adapter = FakeAdapter([ScriptedReply(text="done", input_tokens=200, output_tokens=80)])
    run = await run_agent(
        session=db_session,
        agent_id="cost-agent",
        model_adapter=adapter,
    )
    await db_session.commit()

    assert run.cost_input_tokens == 200
    assert run.cost_output_tokens == 80


@pytest.mark.asyncio
async def test_user_message_overrides_goal(db_session: AsyncSession) -> None:
    """Explicit user_message is passed to the model rather than agent.goal."""
    adapter = fake("custom input processed")

    async with db_session.begin():
        await repo.create_agent(
            db_session,
            **_make_agent_kwargs(agent_id="msg-agent", goal="Default goal"),
        )

    run = await run_agent(
        session=db_session,
        agent_id="msg-agent",
        user_message="Custom user message",
        model_adapter=adapter,
    )
    await db_session.commit()

    # The stored user_message on the run row should reflect the explicit message
    assert run.user_message == "Custom user message"
    # The model received exactly one request with the custom message content
    assert len(adapter.requests) == 1
    msg_content = adapter.requests[0].messages[0].content
    text = "".join(b.text for b in msg_content if hasattr(b, "text"))
    assert "Custom user message" in text


@pytest.mark.asyncio
async def test_goal_used_as_message_when_no_user_message(db_session: AsyncSession) -> None:
    """When user_message is None, agent.goal is used as the effective message."""
    adapter = fake("used goal")

    async with db_session.begin():
        await repo.create_agent(
            db_session,
            **_make_agent_kwargs(agent_id="goal-agent", goal="Accomplish the goal"),
        )

    run = await run_agent(
        session=db_session,
        agent_id="goal-agent",
        model_adapter=adapter,
    )
    await db_session.commit()

    assert run.user_message == "Accomplish the goal"


@pytest.mark.asyncio
async def test_agent_not_found_raises(db_session: AsyncSession) -> None:
    """run_agent raises ValueError for an unknown agent_id."""
    with pytest.raises(ValueError, match="not found"):
        await run_agent(
            session=db_session,
            agent_id="ghost-agent",
            model_adapter=fake("shouldn't reach here"),
        )


@pytest.mark.asyncio
async def test_completed_at_set_on_success(db_session: AsyncSession) -> None:
    """completed_at timestamp is populated when a run finishes."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_make_agent_kwargs(agent_id="ts-agent"))

    run = await run_agent(
        session=db_session,
        agent_id="ts-agent",
        model_adapter=fake("ok"),
    )
    await db_session.commit()

    assert run.completed_at is not None
