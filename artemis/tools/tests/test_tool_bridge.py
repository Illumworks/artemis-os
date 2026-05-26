"""P2 — Tool bridge tests: registry ↔ executor wiring, FakeAdapter, no real API calls."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import Tool
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent
from artemis.tools.registry import _TOOL_FACTORIES, get_factory, register_tool


def _agent_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "test.bridge.agent",
        "name": "Bridge Test",
        "goal": "Test",
        "system_prompt": "Tester.",
        "tools": [],
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_registered_tool_is_called(db_session: AsyncSession) -> None:
    """Registered fake tool is invoked when LLM emits tool_use."""
    tool_name = "fake.tool.bridge_test_1"
    impl_calls: list[dict[str, Any]] = []

    fake_tool_def = Tool(
        name=tool_name,
        description="Fake.",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}},
    )

    async def fake_impl(arguments: dict[str, Any]) -> str:
        impl_calls.append(arguments)
        return "fake-result"

    def fake_factory(ctx):  # type: ignore[no-untyped-def]
        return (fake_tool_def, fake_impl)

    try:
        register_tool(tool_name, fake_factory)
        async with db_session.begin():
            await repo.create_agent(
                db_session, **_agent_kwargs(agent_id="t.b.fake1", tools=[tool_name])
            )

        adapter = FakeAdapter(
            [
                ScriptedReply(
                    tool_calls=[("tu-1", tool_name, {"msg": "hello"})], stop_reason="tool_use"
                ),
                ScriptedReply(text="Done!", stop_reason="end_turn"),
            ]
        )

        run = await run_agent(session=db_session, agent_id="t.b.fake1", model_adapter=adapter)
        await db_session.commit()

        assert run.status == "completed"
        assert len(impl_calls) == 1
        assert impl_calls[0]["msg"] == "hello"
        assert len(adapter.requests) == 2
    finally:
        _TOOL_FACTORIES.pop(tool_name, None)


@pytest.mark.asyncio
async def test_unknown_tool_name_logs_warning_and_completes(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """Unregistered tool name: run completes, single WARNING logged."""
    tool_name = "nonexistent.tool.xyz"
    assert get_factory(tool_name) is None

    async with db_session.begin():
        await repo.create_agent(
            db_session, **_agent_kwargs(agent_id="t.b.unknown", tools=[tool_name])
        )

    adapter = FakeAdapter([ScriptedReply(text="Done.", stop_reason="end_turn")])
    with caplog.at_level(logging.WARNING, logger="artemis.builders.executor"):
        run = await run_agent(session=db_session, agent_id="t.b.unknown", model_adapter=adapter)
    await db_session.commit()

    assert run.status == "completed"
    assert any(tool_name in msg for msg in caplog.messages)
    assert len(adapter.requests) == 1


@pytest.mark.asyncio
async def test_empty_tools_list_passes_none_to_run_turn(db_session: AsyncSession) -> None:
    """tools=[] → tools=None passed to run_turn (backward-compat)."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="t.b.empty", tools=[]))

    adapter = FakeAdapter([ScriptedReply(text="Fine.", stop_reason="end_turn")])
    run = await run_agent(session=db_session, agent_id="t.b.empty", model_adapter=adapter)
    await db_session.commit()

    assert run.status == "completed"
    assert len(adapter.requests) == 1
    assert adapter.requests[0].tools is None
