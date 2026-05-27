"""Tests for agent_executor instruction synthesis (Part A of F6 fix).

Verifies that execute_agent_node passes an imperative user_message to run_agent
depending on agent_id prefix, and that a per-node config["instruction"] override wins.
No real LLM calls — run_agent is fully mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, _patch, patch

import pytest

pytestmark = pytest.mark.asyncio

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_fake_agent(agent_id: str) -> MagicMock:
    agent = MagicMock()
    agent.id = agent_id
    agent.agent_id = agent_id
    agent.tools = []
    agent.reason_codes_emitted = []
    agent.provider = None
    agent.fallback_provider = None
    return agent


def _make_fake_run() -> MagicMock:
    run = MagicMock()
    run.run_id = "fake-run-0000"
    run.status = "completed"
    run.error = None
    run.cost_input_tokens = 10
    run.cost_output_tokens = 5
    return run


def _make_fake_adapter() -> MagicMock:
    return MagicMock()


def _node(agent_id: str, config_extra: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg: dict[str, Any] = {"agent_id": agent_id}
    if config_extra:
        cfg.update(config_extra)
    return {"id": "n1", "type": "agent_invocation", "label": "n1", "config": cfg}


# ── shared patch context ───────────────────────────────────────────────────────


def _patch_deps(
    agent_id: str, fake_run: MagicMock, captured: list[str | None]
) -> tuple[_patch, _patch, _patch]:  # type: ignore[type-arg]
    """Return a context manager stack that patches get_agent + run_agent.

    Both are lazy-imported inside execute_agent_node, so we patch at the source
    module where each name lives at call time.
    """

    async def _fake_run_agent(**kwargs: Any) -> MagicMock:
        captured.append(kwargs.get("user_message"))
        return fake_run

    async def _fake_get_agent(_session: Any, _agent_id: str) -> MagicMock:
        return _make_fake_agent(agent_id)

    return (
        # run_agent is imported from artemis.builders.executor inside the function
        patch("artemis.builders.executor.run_agent", new=_fake_run_agent),
        # get_agent is imported from artemis.builders.repository inside the function
        patch("artemis.builders.repository.get_agent", new=_fake_get_agent),
        # get_credentials_for_tool — patch to no-op so connector check passes
        patch(
            "artemis.connectors.resolver.get_credentials_for_tool",
            new=AsyncMock(return_value=None),
        ),
    )


async def _invoke(node: dict[str, Any], agent_id: str) -> tuple[dict[str, Any], list[str | None]]:
    """Execute the node and return (result, captured_user_messages)."""
    from artemis.pipelines.node_executors.agent_executor import execute_agent_node

    captured: list[str | None] = []
    fake_run = _make_fake_run()

    p1, p2, p3 = _patch_deps(agent_id, fake_run, captured)

    # Also patch get_agent_context to avoid DB calls for output text extraction
    async def _fake_get_ctx(*_args: Any, **_kwargs: Any) -> MagicMock:
        ctx = MagicMock()
        ctx.value = ""
        return ctx

    with p1, p2, p3, patch("artemis.builders.repository.get_agent_context", new=_fake_get_ctx):
        result = await execute_agent_node(
            node=node,
            node_states={},
            session=AsyncMock(),
            run_id="run-test",
            model_adapter=_make_fake_adapter(),
        )
    return result, captured


# ── test cases ────────────────────────────────────────────────────────────────


async def test_scout_instruction_contains_signal_queue_write() -> None:
    """Scout node: user_message contains 'signal_queue.write' AND 'Execute your scan'."""
    agent_id = "marketing.scout.regional_news"
    node = _node(agent_id)
    result, captured = await _invoke(node, agent_id)
    assert result["status"] == "succeeded"
    assert len(captured) == 1
    msg = captured[0] or ""
    assert "signal_queue.write" in msg, f"Expected signal_queue.write in: {msg!r}"
    assert "Execute your scan" in msg, f"Expected 'Execute your scan' in: {msg!r}"


async def test_config_instruction_override_wins() -> None:
    """Per-node config['instruction'] wins over synthesized role-based default."""
    agent_id = "marketing.scout.legislative"
    node = _node(agent_id, {"instruction": "custom task"})
    result, captured = await _invoke(node, agent_id)
    assert result["status"] == "succeeded"
    assert captured[0] == "custom task"


async def test_qualifier_instruction() -> None:
    """Qualifier node: user_message is the qualifier imperative."""
    agent_id = "marketing.qualifier.cross_reference"
    node = _node(agent_id)
    result, captured = await _invoke(node, agent_id)
    assert result["status"] == "succeeded"
    msg = captured[0] or ""
    assert "Process the pending signals NOW" in msg, f"Expected qualifier imperative in: {msg!r}"


async def test_generic_fallback_instruction() -> None:
    """Non-marketing agent_id: user_message is the generic fallback."""
    agent_id = "some.other.agent"
    node = _node(agent_id)
    result, captured = await _invoke(node, agent_id)
    assert result["status"] == "succeeded"
    msg = captured[0] or ""
    assert "Execute your task now" in msg, f"Expected generic fallback in: {msg!r}"
    assert "autonomously" in msg
