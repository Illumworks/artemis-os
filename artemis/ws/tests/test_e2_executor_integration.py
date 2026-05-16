"""Integration tests: running run_agent publishes the expected WS event sequence.

Uses FakeAdapter (no real Anthropic calls) and a DB session (requires Postgres).
A mock WebSocketManager captures broadcast calls without an actual WS connection.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.executor import run_agent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_id": "ws-int-agent",
        "name": "WS Integration Agent",
        "goal": "Test WS events",
        "system_prompt": "You are a test agent.",
        "tools": [],
        "model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Mock WS manager fixture
# ---------------------------------------------------------------------------


class _CapturingManager:
    """Captures broadcast calls without real WebSockets."""

    def __init__(self) -> None:
        self.broadcasts: list[dict[str, Any]] = []

    async def broadcast(self, room: str, event: dict[str, Any]) -> None:
        self.broadcasts.append({"room": room, "event": event})

    def event_types(self) -> list[str]:
        return [b["event"]["type"] for b in self.broadcasts]


# ---------------------------------------------------------------------------
# Tests — simple text reply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_agent_publishes_started_event(db_session: AsyncSession) -> None:
    """run_agent broadcasts agent_run.started immediately after creating the row."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs())

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        run = await run_agent(
            session=db_session,
            agent_id="ws-int-agent",
            model_adapter=FakeAdapter([ScriptedReply(text="done")]),
        )
    await db_session.commit()

    assert run.status == "completed"
    types = capturing.event_types()
    assert "agent_run.started" in types


@pytest.mark.asyncio
async def test_run_agent_publishes_message_event(db_session: AsyncSession) -> None:
    """run_agent broadcasts agent_run.message for the assistant reply."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-msg-agent"))

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        await run_agent(
            session=db_session,
            agent_id="ws-msg-agent",
            model_adapter=FakeAdapter([ScriptedReply(text="hello world")]),
        )
    await db_session.commit()

    types = capturing.event_types()
    assert "agent_run.message" in types


@pytest.mark.asyncio
async def test_run_agent_publishes_completed_event(db_session: AsyncSession) -> None:
    """run_agent broadcasts agent_run.completed after on_done fires."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-done-agent"))

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        await run_agent(
            session=db_session,
            agent_id="ws-done-agent",
            model_adapter=FakeAdapter([ScriptedReply(text="finished")]),
        )
    await db_session.commit()

    types = capturing.event_types()
    assert "agent_run.completed" in types


@pytest.mark.asyncio
async def test_run_agent_publishes_failed_event_on_exception(db_session: AsyncSession) -> None:
    """run_agent broadcasts agent_run.failed when the adapter raises."""

    class BoomAdapter:
        async def complete(self, _req: Any) -> Any:
            raise RuntimeError("bang")

    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-fail-agent"))

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        run = await run_agent(
            session=db_session,
            agent_id="ws-fail-agent",
            model_adapter=BoomAdapter(),
        )
    await db_session.commit()

    assert run.status == "failed"
    types = capturing.event_types()
    assert "agent_run.failed" in types


@pytest.mark.asyncio
async def test_run_agent_event_sequence_order(db_session: AsyncSession) -> None:
    """Events arrive in the expected order: started → message → completed."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-seq-agent"))

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        await run_agent(
            session=db_session,
            agent_id="ws-seq-agent",
            model_adapter=FakeAdapter([ScriptedReply(text="seq done")]),
        )
    await db_session.commit()

    types = capturing.event_types()
    assert types[0] == "agent_run.started"
    # message comes before completed
    assert types.index("agent_run.message") < types.index("agent_run.completed")


@pytest.mark.asyncio
async def test_run_agent_started_payload(db_session: AsyncSession) -> None:
    """agent_run.started payload contains agent_id and user_message."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-payload-agent"))

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        await run_agent(
            session=db_session,
            agent_id="ws-payload-agent",
            user_message="My custom message",
            model_adapter=FakeAdapter([ScriptedReply(text="ok")]),
        )
    await db_session.commit()

    started = next(b for b in capturing.broadcasts if b["event"]["type"] == "agent_run.started")
    assert started["event"]["payload"]["agent_id"] == "ws-payload-agent"
    assert started["event"]["payload"]["user_message"] == "My custom message"


@pytest.mark.asyncio
async def test_run_agent_completed_payload_has_tokens(db_session: AsyncSession) -> None:
    """agent_run.completed payload includes token counts."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-tok-agent"))

    adapter = FakeAdapter([ScriptedReply(text="done", input_tokens=150, output_tokens=75)])
    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        await run_agent(
            session=db_session,
            agent_id="ws-tok-agent",
            model_adapter=adapter,
        )
    await db_session.commit()

    completed = next(b for b in capturing.broadcasts if b["event"]["type"] == "agent_run.completed")
    payload = completed["event"]["payload"]
    assert payload["input_tokens"] == 150
    assert payload["output_tokens"] == 75


@pytest.mark.asyncio
async def test_run_agent_all_events_share_run_id(db_session: AsyncSession) -> None:
    """Every broadcast event carries the same run_id."""
    async with db_session.begin():
        await repo.create_agent(db_session, **_agent_kwargs(agent_id="ws-rid-agent"))

    capturing = _CapturingManager()
    with patch("artemis.builders.executor.ws_manager", capturing):
        run = await run_agent(
            session=db_session,
            agent_id="ws-rid-agent",
            model_adapter=FakeAdapter([ScriptedReply(text="ok")]),
        )
    await db_session.commit()

    for b in capturing.broadcasts:
        assert b["room"] == run.run_id
        assert b["event"]["run_id"] == run.run_id
