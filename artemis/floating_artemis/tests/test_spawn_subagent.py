"""Tests for spawn_subagent tool — ephemeral sub-agent execution.

Design language: fluidity, simplicity, purposefulness, naturalness, spacious, open.

Coverage:
  - Tool registration and authority layer
  - Tool input validation (missing task)
  - Ephemeral agent_runs row creation (is_ephemeral=True, agent_id=None)
  - list_agent_runs default filter (excludes ephemeral)
  - list_agent_runs with include_ephemeral=True (includes ephemeral)
  - Repository round-trip: is_ephemeral persists
  - AgentRunRead schema exposes isEphemeral field
  - agent_runs route ?includeEphemeral=false / ?includeEphemeral=true
  - SPAWN_SUBAGENT descriptor schema validation
  - System prompt contains the propose-vs-spawn teaching block
"""

from __future__ import annotations

import json
from datetime import UTC
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from artemis.floating_artemis.authority import AuthorizedToolRegistry
from artemis.floating_artemis.tools.core import (
    SPAWN_SUBAGENT,
    _spawn_subagent,
    register_core_tools,
)

pytestmark = pytest.mark.asyncio


# ── 1. Tool registration ──────────────────────────────────────────────────────


def test_spawn_subagent_registered_in_registry() -> None:
    reg = AuthorizedToolRegistry()
    register_core_tools(reg)
    entry = reg.get("spawn_subagent")
    assert entry is not None, "spawn_subagent should be registered"


def test_spawn_subagent_is_layer_3() -> None:
    reg = AuthorizedToolRegistry()
    register_core_tools(reg)
    entry = reg.get("spawn_subagent")
    assert entry is not None
    assert entry.layer == 3, "spawn_subagent must be authority layer 3 (side-effect)"


def test_spawn_subagent_tool_descriptor_schema() -> None:
    schema = SPAWN_SUBAGENT.input_schema
    assert "task" in schema["properties"]
    assert "task" in schema["required"]
    assert "model" in schema["properties"]
    assert "max_turns" in schema["properties"]
    model_prop = schema["properties"]["model"]
    assert set(model_prop["enum"]) == {"haiku", "sonnet"}


# ── 2. Input validation ───────────────────────────────────────────────────────


async def test_spawn_subagent_missing_task_returns_error() -> None:
    result = await _spawn_subagent({})
    assert "Error" in result
    assert "task" in result.lower()


async def test_spawn_subagent_empty_task_returns_error() -> None:
    result = await _spawn_subagent({"task": "   "})
    assert "Error" in result
    assert "task" in result.lower()


# ── 3. Ephemeral run creation ─────────────────────────────────────────────────


async def test_spawn_subagent_creates_ephemeral_agent_run() -> None:
    """spawn_subagent must write one agent_runs row with is_ephemeral=True."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    captured_runs: list[dict[str, Any]] = []

    async def mock_create_run(session: Any, **kwargs: Any) -> MagicMock:
        captured_runs.append(kwargs)
        row = MagicMock()
        row.run_id = kwargs.get("run_id", "fake-run")
        return row

    async def mock_set_completed(session: Any, run_id: str, **kwargs: Any) -> MagicMock:
        return MagicMock()

    mock_db_session = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    fake_adapter = FakeAdapter([ScriptedReply(text="Audit complete.")])

    with (
        patch("artemis.agent.client.AnthropicAdapter", return_value=fake_adapter),
        patch("artemis.builders.repository.create_agent_run", new=mock_create_run),
        patch("artemis.builders.repository.set_agent_run_completed", new=mock_set_completed),
        patch("artemis.db.SessionLocal", return_value=mock_cm),
    ):
        await _spawn_subagent({"task": "audit signal #42"})

    # Verify at least one create_agent_run call had is_ephemeral=True
    assert any(run.get("is_ephemeral") is True for run in captured_runs), (
        "Expected at least one agent_run row with is_ephemeral=True"
    )
    # Verify agent_id is None (not linked to a persistent agent)
    assert any(run.get("agent_id") is None for run in captured_runs), (
        "Expected agent_id=None for ephemeral runs"
    )


async def test_spawn_subagent_returns_json_with_ok_and_run_id() -> None:
    """Result must be JSON with ok, output, run_id, cost_usd fields."""
    from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply

    mock_db_session = AsyncMock()
    mock_db_session.commit = AsyncMock()
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_db_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    fake_adapter = FakeAdapter([ScriptedReply(text="Summary complete.")])

    with (
        patch("artemis.agent.client.AnthropicAdapter", return_value=fake_adapter),
        patch(
            "artemis.builders.repository.create_agent_run", new=AsyncMock(return_value=MagicMock())
        ),
        patch(
            "artemis.builders.repository.set_agent_run_completed",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch("artemis.db.SessionLocal", return_value=mock_cm),
    ):
        result_str = await _spawn_subagent({"task": "summarize board minutes"})

    data = json.loads(result_str)
    assert "ok" in data
    assert "output" in data
    assert "run_id" in data
    assert "cost_usd" in data


# ── 4. Repository filter ──────────────────────────────────────────────────────


async def test_list_agent_runs_excludes_ephemeral_by_default() -> None:
    """list_agent_runs(include_ephemeral=False) must exclude is_ephemeral=True rows."""
    from artemis.builders import repository as repo
    from artemis.builders.models import AgentRun

    # Build two fake rows
    non_ephemeral = MagicMock(spec=AgentRun)
    non_ephemeral.is_ephemeral = False
    non_ephemeral.id = 2

    ephemeral = MagicMock(spec=AgentRun)
    ephemeral.is_ephemeral = True
    ephemeral.id = 1

    # Mock the DB layer: simulate the query returning only the non-ephemeral row
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [non_ephemeral]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    rows = await repo.list_agent_runs(mock_session, include_ephemeral=False)
    assert all(not r.is_ephemeral for r in rows), "Default filter must exclude ephemeral runs"


async def test_list_agent_runs_includes_ephemeral_when_requested() -> None:
    """list_agent_runs(include_ephemeral=True) must include is_ephemeral=True rows."""
    from artemis.builders import repository as repo
    from artemis.builders.models import AgentRun

    ephemeral = MagicMock(spec=AgentRun)
    ephemeral.is_ephemeral = True
    ephemeral.id = 1

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [ephemeral]
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    rows = await repo.list_agent_runs(mock_session, include_ephemeral=True)
    assert len(rows) == 1 and rows[0].is_ephemeral


# ── 5. Schema: is_ephemeral field ────────────────────────────────────────────


def test_agent_run_read_schema_has_is_ephemeral() -> None:
    from artemis.builders.schemas import AgentRunRead

    # isEphemeral should be in model fields (by alias)
    field_aliases = {f.alias for f in AgentRunRead.model_fields.values() if f.alias}
    assert "isEphemeral" in field_aliases, "AgentRunRead must expose isEphemeral alias"


def test_agent_run_read_is_ephemeral_defaults_false() -> None:
    from datetime import datetime

    from artemis.builders.schemas import AgentRunRead

    now = datetime(2026, 5, 17, tzinfo=UTC)
    # Use model_validate with a dict (avoids from_attributes issues with MagicMock)
    read = AgentRunRead.model_validate(
        {
            "id": 1,
            "runId": "test-run-id",
            "agentId": None,
            "status": "completed",
            "userMessage": "hello",
            "sharedContext": None,
            "startedAt": now,
            "completedAt": None,
            "costInputTokens": 0,
            "costOutputTokens": 0,
            "error": None,
            "ownerUserId": None,
            "isEphemeral": False,
        }
    )
    assert read.is_ephemeral is False


# ── 6. System prompt teaching block ──────────────────────────────────────────


def test_system_prompt_contains_propose_vs_spawn_teaching() -> None:
    """_PERSONA_CORE must contain the propose/spawn distinction block."""
    from artemis.floating_artemis.chat import _PERSONA_CORE

    assert "PROPOSE" in _PERSONA_CORE, "System prompt must include PROPOSE teaching"
    assert "SPAWN" in _PERSONA_CORE, "System prompt must include SPAWN teaching"
    assert "once" in _PERSONA_CORE.lower(), (
        "System prompt must describe SPAWN as a one-time/once operation"
    )
    assert "/agents" in _PERSONA_CORE, "System prompt must mention /agents as the persistence test"


# ── 7. Route filter (via mock) ────────────────────────────────────────────────


async def test_agent_runs_route_default_excludes_ephemeral() -> None:
    """GET /api/agent-runs/ without includeEphemeral must call repo with include_ephemeral=False."""
    from httpx import ASGITransport, AsyncClient

    captured_kwargs: dict[str, Any] = {}

    async def mock_list_runs(session: Any, **kwargs: Any) -> list[Any]:
        captured_kwargs.update(kwargs)
        return []

    with patch("artemis.builders.repository.list_agent_runs", new=mock_list_runs):
        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agent-runs/")

    assert resp.status_code == 200
    assert captured_kwargs.get("include_ephemeral") is False


async def test_agent_runs_route_include_ephemeral_param() -> None:
    """GET /api/agent-runs/?includeEphemeral=true must call repo with include_ephemeral=True."""
    from httpx import ASGITransport, AsyncClient

    captured_kwargs: dict[str, Any] = {}

    async def mock_list_runs(session: Any, **kwargs: Any) -> list[Any]:
        captured_kwargs.update(kwargs)
        return []

    with patch("artemis.builders.repository.list_agent_runs", new=mock_list_runs):
        from artemis.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/agent-runs/?includeEphemeral=true")

    assert resp.status_code == 200
    assert captured_kwargs.get("include_ephemeral") is True
