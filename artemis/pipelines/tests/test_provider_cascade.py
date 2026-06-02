"""Tests for provider cascade wiring in the pipeline execution path.

Locks in three behaviours that previously regressed:

1. The pipeline executor resolves the adapter via the cascade (so an agent
   declared ``provider="claude-code"`` is not silently routed through
   ``AnthropicAdapter()``).
2. When the cascade can find nothing, the agent node fails with a clear
   "No LLM provider available" message — not a cryptic auth ``TypeError``
   bubbling up from the Anthropic SDK.
3. ``pipeline_runs.node_states`` is JSONB+MutableDict, so in-place dict
   mutations between flushes survive the round-trip to Postgres. Without this
   the trigger node stayed pinned at ``status="running"`` even after a
   downstream node failed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo
from artemis.pipelines.executor import PipelineExecutor

pytestmark = pytest.mark.asyncio


_TRUNCATE_SQL = text(
    "TRUNCATE pipeline_runs, pipelines, agent_runs, agents RESTART IDENTITY CASCADE"
)


async def _reset(session: AsyncSession) -> None:
    await session.execute(_TRUNCATE_SQL)
    await session.commit()


def _node(node_id: str, node_type: str, config: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": node_id,
        "config": config or {},
        "position": {"x": 0.0, "y": 0.0},
    }


def _edge(src: str, tgt: str) -> dict[str, Any]:
    return {
        "id": f"e_{src}_{tgt}",
        "source_node_id": src,
        "target_node_id": tgt,
        "condition": None,
        "data_shape": None,
    }


async def _insert_agent(
    session: AsyncSession, agent_id: str, provider: str = "claude-code"
) -> None:
    await session.execute(
        text(
            "INSERT INTO agents (agent_id, name, tools, model, provider) "
            "VALUES (:aid, :aid, '[]'::jsonb, 'claude-haiku-4-5', :prov)"
        ),
        {"aid": agent_id, "prov": provider},
    )


# ── 1. Cascade is consulted using the agent's provider field ──────────────────


async def test_agent_executor_resolves_via_agent_provider(db_session: AsyncSession) -> None:
    """agent_executor must call resolve_adapter(agent.provider, agent.fallback_provider)."""
    await _reset(db_session)
    async with db_session.begin():
        await _insert_agent(db_session, "scout.test", provider="claude-code")

    nodes = [
        _node("trigger", "trigger_manual"),
        _node("scout", "agent_invocation", {"agent_id": "scout.test"}),
    ]
    edges = [_edge("trigger", "scout")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(
            db_session, name="cascade-test", nodes=nodes, edges=edges
        )
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="queued",
            trigger="manual",
            triggered_by="test",
        )

    seen: dict[str, Any] = {}

    class _StubAdapter:
        pass

    def _capture_resolve(provider: str | None, fallback: str | None = None, **_: Any) -> Any:
        seen["provider"] = provider
        seen["fallback"] = fallback
        return _StubAdapter()

    # Patch resolve_adapter where agent_executor imports it (inside the function body).
    # Also short-circuit run_agent so we don't actually try to invoke an LLM.
    fake_run = AsyncMock()
    fake_run.return_value = type(
        "AR",
        (),
        {
            "status": "completed",
            "cost_input_tokens": 0,
            "cost_output_tokens": 0,
            "run_id": "fake-run-id",
            "error": None,
        },
    )()

    with (
        patch("artemis.providers.resolver.resolve_adapter", side_effect=_capture_resolve),
        patch("artemis.builders.executor.run_agent", new=fake_run),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    assert seen.get("provider") == "claude-code"
    # fallback may be None (we didn't set one); the important bit is the call happened
    assert "fallback" in seen


# ── 2. Cascade exhaustion surfaces a clean failure ───────────────────────────


async def test_no_provider_available_yields_clean_node_failure(db_session: AsyncSession) -> None:
    """When the cascade can't construct any adapter, the agent node returns a
    failed result with a useful error string — not an SDK TypeError."""
    from artemis.providers.resolver import NoProviderAvailableError

    await _reset(db_session)
    async with db_session.begin():
        await _insert_agent(db_session, "scout.empty", provider="claude-code")

    nodes = [
        _node("trigger", "trigger_manual"),
        _node("scout", "agent_invocation", {"agent_id": "scout.empty"}),
    ]
    edges = [_edge("trigger", "scout")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(
            db_session, name="empty-cascade", nodes=nodes, edges=edges
        )
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="queued",
            trigger="manual",
            triggered_by="test",
        )

    with patch(
        "artemis.providers.resolver.resolve_adapter",
        side_effect=NoProviderAvailableError("nothing in cascade"),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    async with db_session.begin():
        refreshed = await repo.get_pipeline_run(db_session, run.id)
        assert refreshed.status == "failed"
        # The error message should mention "provider", not the SDK's auth phrasing.
        assert "provider" in (refreshed.error_message or "").lower()
        # And it must not be the old anthropic SDK auth TypeError text:
        assert "Could not resolve authentication method" not in (refreshed.error_message or "")


# ── 3. Trigger node never stuck "running" after downstream failure ───────────


async def test_trigger_state_persists_after_downstream_failure(
    db_session: AsyncSession,
) -> None:
    """Regression: with JSONB+MutableDict, every node_states write reaches the
    DB. Trigger should end at ``succeeded`` even when the agent node fails.

    We verify against a *fresh* SQL fetch (not the ORM cache) to confirm the
    bytes actually made it to Postgres.
    """
    await _reset(db_session)
    async with db_session.begin():
        await _insert_agent(db_session, "scout.boom", provider="claude-code")

    nodes = [
        _node("trigger_scheduled", "trigger_scheduled"),
        _node("agent_boom", "agent_invocation", {"agent_id": "scout.boom"}),
    ]
    edges = [_edge("trigger_scheduled", "agent_boom")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(
            db_session, name="trigger-persist", nodes=nodes, edges=edges
        )
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=pipeline.id,
            status="queued",
            trigger="manual",
            triggered_by="test",
        )
    run_id = run.id

    # Make the agent node fail mid-dispatch (any exception is fine).
    with patch(
        "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
        new=AsyncMock(
            return_value={
                "status": "failed",
                "error": "synthetic boom",
                "output_summary": "",
                "cost_usd": 0.0,
            }
        ),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run_id)
            await executor.run(db_session)

    # Pull node_states straight from Postgres with a fresh query — bypass any
    # ORM-cached attribute reads.
    async with db_session.begin():
        row = await db_session.execute(
            text("SELECT status, node_states FROM pipeline_runs WHERE id = :id"),
            {"id": run_id},
        )
        status, node_states = row.one()

    assert status == "failed"
    assert isinstance(node_states, dict)
    assert "trigger_scheduled" in node_states, (
        f"trigger node missing from node_states: {node_states}"
    )
    assert node_states["trigger_scheduled"]["status"] == "succeeded", (
        f"trigger node stuck at {node_states['trigger_scheduled']['status']!r}; "
        "expected 'succeeded' (regression of trigger-stuck-running bug)"
    )
    assert "agent_boom" in node_states, f"agent node missing from node_states: {node_states}"
    assert node_states["agent_boom"]["status"] == "failed"
