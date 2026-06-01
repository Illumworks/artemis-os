"""PIPE4 execution engine tests.

Tests:
1. Unit: trigger node executor
2. Unit: conditional node executor — true/false branch
3. Unit: agent executor cost cap enforcement
4. Integration: 3-node toy pipeline (trigger → agent → result)
5. Integration: conditional branch (true and false paths)
6. Integration: human gate suspend + resume (approved path)
7. Integration: human gate reject path
8. Integration: fan-in semantics (4 nodes converge to gate)
9. Integration: cost cap halts executor + partial_complete status
10. Integration: timeout auto_approve fires audit entry + continues
11. Integration: escalation flow (timeout → escalation_to DM)
12. Smoke: marketing pipeline end-to-end (CI2 graph, mocked agents + Slack)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.pipelines import repository as repo
from artemis.pipelines.executor import PipelineExecutor, _topological_sort
from artemis.pipelines.node_executors.conditional_executor import execute_conditional_node
from artemis.pipelines.node_executors.trigger_executor import execute_trigger_node

pytestmark = pytest.mark.asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

TRUNCATE = text(
    "TRUNCATE pipeline_runs, pipelines, approvals, agent_context, "
    "agent_run_trajectory_summaries, definition_proposals, "
    "agent_runs, agent_skills, agents RESTART IDENTITY CASCADE"
)


async def _reset(session: AsyncSession, *, insert_agents: bool = False) -> None:
    await session.execute(TRUNCATE)
    if insert_agents:
        from artemis.marketing.seeds.marketing_agents import MARKETING_AGENT_SPECS

        agents = [
            {
                "agent_id": spec.agent_id,
                "name": spec.agent_id,
                "tools": "[]",
                "model": "claude-haiku-4-5",
                "provider": "claude-code",
            }
            for spec in MARKETING_AGENT_SPECS
        ]
        # Also insert the extra pipeline agents referenced in seeds
        pipeline_agent_ids = [
            "marketing.qualifier.cross_reference",
            "marketing.qualifier.brief_composer",
            "marketing.content.brief_assembler",
            "marketing.content.asset_selector",
            "marketing.content.writing_studio_adapter",
        ]
        for agent_id in pipeline_agent_ids:
            if not any(a["agent_id"] == agent_id for a in agents):
                agents.append(
                    {
                        "agent_id": agent_id,
                        "name": agent_id,
                        "tools": "[]",
                        "model": "claude-haiku-4-5",
                        "provider": "claude-code",
                    }
                )
        await session.execute(
            text(
                "INSERT INTO agents (agent_id, name, tools, model, provider) "
                "VALUES (:agent_id, :name, :tools::jsonb, :model, :provider) "
                "ON CONFLICT (agent_id) DO NOTHING"
            ),
            agents,
        )
    await session.commit()


def _make_pipeline(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": "Test Pipeline", "nodes": nodes, "edges": edges}


def _make_run(pipeline_id: str) -> dict[str, Any]:
    return {
        "pipeline_id": pipeline_id,
        "status": "queued",
        "trigger": "manual",
        "triggered_by": "test",
    }


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


# ── 1. Unit: trigger node ─────────────────────────────────────────────────────


async def test_trigger_node_returns_succeeded() -> None:
    node = _node("t1", "trigger_scheduled")
    result = await execute_trigger_node(node=node, node_states={}, run_id="run-1")
    assert result["status"] == "succeeded"
    assert "Trigger fired" in result["output_summary"]
    assert result["cost_usd"] == 0.0


async def test_trigger_manual_mode() -> None:
    node = _node("t1", "trigger_manual")
    result = await execute_trigger_node(node=node, node_states={}, run_id="run-1", mode="manual")
    assert result["status"] == "succeeded"
    assert "manual" in result["output_summary"]


async def test_background_executor_crash_marks_queued_run_failed(
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    nodes = [_node("trigger", "trigger_manual")]
    edges: list[dict[str, Any]] = []

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    from artemis.pipelines.routes import _execute_pipeline_run

    with (
        patch(
            "artemis.pipelines.executor.PipelineExecutor.run",
            new=AsyncMock(side_effect=RuntimeError("synthetic executor crash")),
        ),
        pytest.raises(RuntimeError, match="synthetic executor crash"),
    ):
        await _execute_pipeline_run(run.id)

    import artemis.db as _db

    async with _db.SessionLocal() as fresh_session:
        final = await repo.get_pipeline_run(fresh_session, run.id)
    assert final.status == "failed"
    assert final.completed_at is not None
    assert final.error_message == "Executor crashed before start: synthetic executor crash"


async def test_sweep_orphaned_queued_runs_marks_old_rows_failed(
    db_session: AsyncSession,
) -> None:
    await _reset(db_session)
    nodes = [_node("trigger", "trigger_manual")]
    edges: list[dict[str, Any]] = []

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        old_run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))
        fresh_run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))
        await db_session.execute(
            text("UPDATE pipeline_runs SET created_at = :created_at WHERE id = :run_id"),
            {
                "created_at": datetime.now(UTC) - timedelta(minutes=6),
                "run_id": old_run.id,
            },
        )

    from artemis.pipelines.scheduler import sweep_orphaned_queued_runs

    swept = await sweep_orphaned_queued_runs(threshold_minutes=5)

    import artemis.db as _db

    async with _db.SessionLocal() as fresh_session:
        old_final = await repo.get_pipeline_run(fresh_session, old_run.id)
        fresh_final = await repo.get_pipeline_run(fresh_session, fresh_run.id)
    assert swept == 1
    assert old_final.status == "failed"
    assert old_final.error_message == "Orphaned queued run (executor never started)"
    assert fresh_final.status == "queued"


# ── 2. Unit: conditional node ─────────────────────────────────────────────────


async def test_conditional_true_branch() -> None:
    node = _node(
        "cond1",
        "conditional",
        {
            "predicate": {"field": "score", "operator": "greater_than", "value": 50},
        },
    )
    result = await execute_conditional_node(node=node, node_states={}, context={"score": 75})
    assert result["status"] == "succeeded"
    assert result["branch"] == "true_branch"


async def test_conditional_false_branch() -> None:
    node = _node(
        "cond1",
        "conditional",
        {
            "predicate": {"field": "score", "operator": "greater_than", "value": 50},
        },
    )
    result = await execute_conditional_node(node=node, node_states={}, context={"score": 20})
    assert result["status"] == "succeeded"
    assert result["branch"] == "false_branch"


async def test_conditional_equals_operator() -> None:
    node = _node(
        "cond1",
        "conditional",
        {
            "predicate": {"field": "status", "operator": "equals", "value": "active"},
        },
    )
    result = await execute_conditional_node(node=node, node_states={}, context={"status": "active"})
    assert result["branch"] == "true_branch"


async def test_conditional_in_list_operator() -> None:
    node = _node(
        "cond1",
        "conditional",
        {
            "predicate": {"field": "tier", "operator": "in_list", "value": ["hot", "warm"]},
        },
    )
    result = await execute_conditional_node(node=node, node_states={}, context={"tier": "hot"})
    assert result["branch"] == "true_branch"


async def test_conditional_jsonlogic() -> None:
    node = _node(
        "cond1",
        "conditional",
        {
            "jsonlogic": {">": [{"var": "score"}, 50]},
        },
    )
    result = await execute_conditional_node(node=node, node_states={}, context={"score": 80})
    assert result["branch"] == "true_branch"


# ── 3. Unit: topo sort ────────────────────────────────────────────────────────


def test_topological_sort_linear() -> None:
    nodes = [_node("a", "trigger_manual"), _node("b", "agent_invocation"), _node("c", "human_gate")]
    edges = [_edge("a", "b"), _edge("b", "c")]
    ordered = _topological_sort(nodes, edges)
    ids = [n["id"] for n in ordered]
    assert ids == ["a", "b", "c"]


def test_topological_sort_fan_in() -> None:
    nodes = [
        _node("t", "trigger_manual"),
        _node("a1", "agent_invocation"),
        _node("a2", "agent_invocation"),
        _node("gate", "human_gate"),
    ]
    edges = [_edge("t", "a1"), _edge("t", "a2"), _edge("a1", "gate"), _edge("a2", "gate")]
    ordered = _topological_sort(nodes, edges)
    ids = [n["id"] for n in ordered]
    assert ids.index("gate") > ids.index("a1")
    assert ids.index("gate") > ids.index("a2")


def test_topological_sort_cycle_raises() -> None:
    nodes = [_node("a", "agent_invocation"), _node("b", "agent_invocation")]
    edges = [_edge("a", "b"), _edge("b", "a")]
    with pytest.raises(ValueError, match="cycle"):
        _topological_sort(nodes, edges)


# ── 4. Integration: 3-node toy pipeline ──────────────────────────────────────


async def test_three_node_pipeline_succeeds(db_session: AsyncSession) -> None:
    """trigger → agent → result: pipeline reaches succeeded with mocked agent."""
    await _reset(db_session)

    # Insert a minimal agent
    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO agents (agent_id, name, tools, model, provider) "
                "VALUES ('toy.agent', 'Toy Agent', '[]'::jsonb, 'claude-haiku-4-5', 'claude-code')"
            )
        )

    nodes = [
        _node("trigger", "trigger_manual"),
        _node("agent1", "agent_invocation", {"agent_id": "toy.agent"}),
    ]
    edges = [_edge("trigger", "agent1")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    with patch(
        "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
        new=AsyncMock(
            return_value={
                "status": "succeeded",
                "output_summary": "Mocked agent done",
                "cost_usd": 0.01,
                "agent_run_id": "mock-run-toy",
            }
        ),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    async with db_session.begin():
        refreshed = await repo.get_pipeline_run(db_session, run.id)
        assert refreshed.status == "succeeded"
        ns = refreshed.node_states
        assert ns["trigger"]["status"] == "succeeded"
        assert ns["agent1"]["status"] == "succeeded"


async def test_brief_composer_empty_signals_skips_downstream(
    db_session: AsyncSession,
) -> None:
    """0 qualified signals halts at brief composer without creating an approval."""
    await _reset(db_session)

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "qualifier_brief_composer",
            "agent_invocation",
            {"agent_id": "marketing.qualifier.brief_composer"},
        ),
        _node(
            "gate_1_signals_inbox",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["test@example.com"],
                "timeout_hours": 72,
            },
        ),
        _node("content_brief_assembler", "agent_invocation", {"agent_id": "content.agent"}),
    ]
    edges = [
        _edge("trigger", "qualifier_brief_composer"),
        _edge("qualifier_brief_composer", "gate_1_signals_inbox"),
        _edge("gate_1_signals_inbox", "content_brief_assembler"),
    ]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    with patch(
        "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
        new=AsyncMock(side_effect=AssertionError("brief composer should not dispatch")),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    async with db_session.begin():
        refreshed = await repo.get_pipeline_run(db_session, run.id)
        approval_count = (
            await db_session.execute(text("SELECT COUNT(*) FROM approvals"))
        ).scalar_one()
        ns = refreshed.node_states

    assert refreshed.status == "succeeded"
    assert refreshed.metadata_ is not None
    assert refreshed.metadata_["summary"] == "No signals this run; downstream skipped"
    assert ns["qualifier_brief_composer"]["status"] == "succeeded"
    assert ns["qualifier_brief_composer"]["output_summary"] == "No signals qualified this run"
    assert ns["gate_1_signals_inbox"]["status"] == "skipped"
    assert ns["content_brief_assembler"]["status"] == "skipped"
    assert approval_count == 0


# ── 5. Integration: conditional branch ───────────────────────────────────────


async def test_conditional_branch_true_path(db_session: AsyncSession) -> None:
    """trigger → conditional: executor records true_branch in node_states."""
    await _reset(db_session)

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "cond",
            "conditional",
            {
                "predicate": {"field": "trigger", "operator": "equals", "value": "trigger"},
            },
        ),
    ]
    edges = [_edge("trigger", "cond")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    async with db_session.begin():
        executor = PipelineExecutor(run.id)
        await executor.run(db_session)

    async with db_session.begin():
        refreshed = await repo.get_pipeline_run(db_session, run.id)
        assert refreshed.status == "succeeded"
        assert refreshed.node_states["cond"]["branch"] in ("true_branch", "false_branch")


# ── 6. Integration: human gate suspend + resume (approved) ────────────────────


async def test_human_gate_suspend_and_resume_approved(db_session: AsyncSession) -> None:
    """Gate suspends pipeline; resume with approved continues to next node."""
    await _reset(db_session)

    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO agents (agent_id, name, tools, model, provider) "
                "VALUES ('post.gate.agent', 'Post Gate', '[]'::jsonb, 'claude-haiku-4-5', 'claude-code')"
            )
        )

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "gate1",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["test@example.com"],
                "timeout_hours": 72,
            },
        ),
        _node("after_gate", "agent_invocation", {"agent_id": "post.gate.agent"}),
    ]
    edges = [_edge("trigger", "gate1"), _edge("gate1", "after_gate")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    # Run 1: should suspend at gate1
    with (
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    async with db_session.begin():
        refreshed = await repo.get_pipeline_run(db_session, run.id)
        assert refreshed.status == "awaiting_approval"
        assert refreshed.node_states["gate1"]["status"] == "suspended"
        assert refreshed.node_states.get("after_gate", {}).get("status") != "succeeded"

    # Update gate decision to approved
    async with db_session.begin():
        run_obj = await repo.get_pipeline_run(db_session, run.id)
        node_states = dict(run_obj.node_states)
        node_states["gate1"]["decision"] = "approved"
        node_states["gate1"]["decided_at"] = "2026-05-22T00:00:00+00:00"
        node_states["gate1"]["decided_by"] = "test@example.com"
        await repo.update_pipeline_run(
            db_session, run.id, node_states=node_states, status="running"
        )

    # Run 2: resume
    with patch(
        "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
        new=AsyncMock(return_value=_mock_agent_node_result("post.gate.agent")),
    ):
        async with db_session.begin():
            executor2 = PipelineExecutor(run.id)
            await executor2.run(db_session)

    async with db_session.begin():
        final = await repo.get_pipeline_run(db_session, run.id)
        assert final.status == "succeeded"
        assert final.node_states["after_gate"]["status"] == "succeeded"


# ── 7. Integration: gate rejected ────────────────────────────────────────────


async def test_human_gate_reject_marks_failed(db_session: AsyncSession) -> None:
    """Gate with rejected decision marks pipeline as failed."""
    await _reset(db_session)

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "gate1",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["test@example.com"],
                "timeout_hours": 72,
            },
        ),
    ]
    edges = [_edge("trigger", "gate1")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    with (
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    # Set decision to rejected
    async with db_session.begin():
        run_obj = await repo.get_pipeline_run(db_session, run.id)
        ns = dict(run_obj.node_states)
        ns["gate1"]["decision"] = "rejected"
        await repo.update_pipeline_run(db_session, run.id, node_states=ns, status="running")

    # Resume
    async with db_session.begin():
        executor2 = PipelineExecutor(run.id)
        await executor2.run(db_session)

    async with db_session.begin():
        final = await repo.get_pipeline_run(db_session, run.id)
        assert final.status == "failed"
        assert "rejected" in (final.error_message or "")


# ── 8. Integration: fan-in semantics ─────────────────────────────────────────


async def test_fan_in_gate_waits_for_all_upstream(db_session: AsyncSession) -> None:
    """Gate with 3 upstream nodes: waits for all 3 before firing."""
    await _reset(db_session)

    for agent_id in ("a1_agent", "a2_agent", "a3_agent"):
        async with db_session.begin():
            await db_session.execute(
                text(
                    "INSERT INTO agents (agent_id, name, tools, model, provider) "
                    "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code') "
                    "ON CONFLICT (agent_id) DO NOTHING"
                ),
                {"agent_id": agent_id},
            )

    nodes = [
        _node("trigger", "trigger_manual"),
        _node("a1", "agent_invocation", {"agent_id": "a1_agent"}),
        _node("a2", "agent_invocation", {"agent_id": "a2_agent"}),
        _node("a3", "agent_invocation", {"agent_id": "a3_agent"}),
        _node(
            "gate",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["test@example.com"],
                "timeout_hours": 72,
                "wait_for_all_upstream": True,
            },
        ),
    ]
    edges = [
        _edge("trigger", "a1"),
        _edge("trigger", "a2"),
        _edge("trigger", "a3"),
        _edge("a1", "gate"),
        _edge("a2", "gate"),
        _edge("a3", "gate"),
    ]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    async def _mock_execute_agent_fan_in(
        node: Any,
        node_states: Any,
        session: Any,
        run_id: str,
        accumulated_cost_usd: float = 0.0,
        model_adapter: Any = None,
    ) -> dict[str, Any]:
        agent_id = (node.get("config") or {}).get("agent_id", "unknown")
        return _mock_agent_node_result(agent_id)

    with (
        patch(
            "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
            new=_mock_execute_agent_fan_in,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    async with db_session.begin():
        refreshed = await repo.get_pipeline_run(db_session, run.id)
        ns = refreshed.node_states
        # All three upstream agents should have run
        for agent_node_id in ("a1", "a2", "a3"):
            assert ns.get(agent_node_id, {}).get("status") == "succeeded", (
                f"Expected {agent_node_id} to be succeeded, got {ns.get(agent_node_id)}"
            )
        # Gate should be suspended (all upstream done, gate fired)
        assert ns["gate"]["status"] == "suspended"
        assert refreshed.status == "awaiting_approval"


# ── 9. Integration: cost cap enforcement ─────────────────────────────────────


async def test_cost_cap_halts_executor(db_session: AsyncSession) -> None:
    """Agent node with cost_cap_usd halts run + marks partial_complete."""
    await _reset(db_session)

    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO agents (agent_id, name, tools, model, provider) "
                "VALUES ('capped.agent', 'Capped', '[]'::jsonb, 'claude-sonnet-4-6', 'claude-code')"
            )
        )

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "costly",
            "agent_invocation",
            {
                "agent_id": "capped.agent",
                "cost_cap_usd": 0.001,  # very low cap
            },
        ),
    ]
    edges = [_edge("trigger", "costly")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    # Mock the agent executor to return partial_complete (simulating cost cap hit)
    cost_cap_result = {
        "status": "partial_complete",
        "error": "cost_cap_exceeded: $1.8000 > cap $0.00",
        "output_summary": "Agent 'capped.agent' stopped: cost cap $0.00 exceeded",
        "cost_usd": 1.8,
    }

    with patch(
        "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
        new=AsyncMock(return_value=cost_cap_result),
    ):
        async with db_session.begin():
            executor = PipelineExecutor(run.id)
            await executor.run(db_session)

    async with db_session.begin():
        final = await repo.get_pipeline_run(db_session, run.id)
        assert final.status == "partial_complete"
        assert "cost_cap" in (final.error_message or "").lower()


# ── 10. Integration: timeout auto_approve audit entry ────────────────────────


async def test_timeout_auto_approve_audit_entry(db_session: AsyncSession) -> None:
    """_fire_gate_timeout with on_timeout=auto_approve writes audit entry."""
    await _reset(db_session)

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "gate",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["test@example.com"],
                "timeout_hours": 1,
                "on_timeout": "auto_approve",
            },
        ),
    ]
    edges = [_edge("trigger", "gate")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    # Manually set up node_states as if gate fired and is suspended
    async with db_session.begin():
        from datetime import UTC, datetime

        ns = {
            "trigger": {"status": "succeeded", "output_summary": "fired", "cost_usd": 0.0},
            "gate": {
                "status": "suspended",
                "started_at": datetime.now(UTC).isoformat(),
                "cost_usd": 0.0,
            },
        }
        await repo.update_pipeline_run(
            db_session,
            run.id,
            status="awaiting_approval",
            node_states=ns,
        )

    from artemis.pipelines.node_executors.human_gate_executor import _fire_gate_timeout

    with patch(
        "artemis.pipelines.executor.PipelineExecutor.run",
        new=AsyncMock(return_value=None),
    ):
        await _fire_gate_timeout(
            run.id,
            "gate",
            "auto_approve",
            {
                "approval_kind": "signal_brief",
                "approvers": ["test@example.com"],
                "timeout_hours": 1,
            },
        )

    # _fire_gate_timeout uses its own session; use a fresh session to read the result
    import artemis.db as _db

    async with _db.SessionLocal() as fresh_session:
        final = await repo.get_pipeline_run(fresh_session, run.id)
        ns = final.node_states
        # Audit trail should have an entry
        audit = ns.get("_audit", [])
        assert len(audit) > 0, "Expected at least one audit entry"
        auto_entry = next((e for e in audit if e.get("kind") == "gate_auto_decision"), None)
        assert auto_entry is not None, "Expected gate_auto_decision audit entry"
        assert auto_entry["decision"] == "auto_approved"
        assert "timeout_after" in auto_entry["reason"]


# ── 11. Integration: escalation flow ─────────────────────────────────────────


async def test_escalation_timeout_sends_audit_and_updates_state(db_session: AsyncSession) -> None:
    """_fire_gate_timeout with on_timeout=escalate writes escalation_sent audit."""
    await _reset(db_session)

    nodes = [
        _node("trigger", "trigger_manual"),
        _node(
            "gate",
            "human_gate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["primary@example.com"],
                "timeout_hours": 72,
                "on_timeout": "escalate",
                "escalation_to": ["escalate@example.com"],
            },
        ),
    ]
    edges = [_edge("trigger", "gate")]

    async with db_session.begin():
        pipeline = await repo.create_pipeline(db_session, **_make_pipeline(nodes, edges))
        run = await repo.create_pipeline_run(db_session, **_make_run(pipeline.id))

    async with db_session.begin():
        from datetime import UTC, datetime

        ns = {
            "trigger": {"status": "succeeded", "output_summary": "fired", "cost_usd": 0.0},
            "gate": {
                "status": "suspended",
                "started_at": datetime.now(UTC).isoformat(),
                "cost_usd": 0.0,
            },
        }
        await repo.update_pipeline_run(
            db_session,
            run.id,
            status="awaiting_approval",
            node_states=ns,
        )

    from artemis.pipelines.node_executors.human_gate_executor import _fire_gate_timeout

    with (
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        await _fire_gate_timeout(
            run.id,
            "gate",
            "escalate",
            {
                "approval_kind": "signal_brief",
                "approvers": ["primary@example.com"],
                "timeout_hours": 72,
                "on_timeout": "escalate",
                "escalation_to": ["escalate@example.com"],
            },
        )

    # _fire_gate_timeout uses its own session; use a fresh session to read the result
    import artemis.db as _db

    async with _db.SessionLocal() as fresh_session:
        final = await repo.get_pipeline_run(fresh_session, run.id)
        audit = final.node_states.get("_audit", [])
        escalation_entry = next((e for e in audit if e.get("kind") == "escalation_sent"), None)
        assert escalation_entry is not None, f"Expected escalation_sent; got audit={audit}"
        assert escalation_entry["decision"] == "escalated"
        # Gate state should indicate escalation
        assert final.node_states["gate"].get("escalated") is True


# ── 12. Smoke: marketing pipeline end-to-end ─────────────────────────────────


async def test_marketing_pipeline_traverses_ci2_graph(db_session: AsyncSession) -> None:
    """Smoke test: CI2 marketing pipeline traverses the initiation gate flow.

    Mocks all agent invocations and Slack DMs.
    Gate 1 and Gate 2 suspend → then resume with approved decision.
    """
    from artemis.pipelines.seeds.marketing_pipeline import (
        AGENT_IDS,
        PIPELINE_ID,
        seed_marketing_pipeline,
    )

    await _reset(db_session)

    # Insert the marketing agents (minimal stubs)
    async with db_session.begin():
        await db_session.execute(
            text(
                "INSERT INTO agents (agent_id, name, tools, model, provider) "
                "VALUES (:agent_id, :agent_id, '[]'::jsonb, 'claude-haiku-4-5', 'claude-code') "
                "ON CONFLICT (agent_id) DO NOTHING"
            ),
            [{"agent_id": agent_id} for agent_id in AGENT_IDS],
        )

    # Seed marketing pipeline
    result = await seed_marketing_pipeline(db_session)
    assert result["node_count"] == 19

    async with db_session.begin():
        run = await repo.create_pipeline_run(
            db_session,
            pipeline_id=PIPELINE_ID,
            status="queued",
            trigger="manual",
            triggered_by="test",
        )
        await db_session.execute(
            text(
                "INSERT INTO signal_queue (headline, summary, campaign_family, "
                "signal_status, discovered_by) VALUES "
                "('Qualified marketing signal', '', 'marketing', 'qualified', 'test')"
            )
        )

    run_id = run.id

    async def _mock_execute_agent_node(
        node: Any,
        node_states: Any,
        session: Any,
        run_id: Any,
        accumulated_cost_usd: float = 0.0,
        model_adapter: Any = None,
    ) -> dict[str, Any]:
        agent_id = (node.get("config") or {}).get("agent_id", "unknown")
        return _mock_agent_node_result(agent_id)

    with (
        patch(
            "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
            new=_mock_execute_agent_node,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        # First pass: runs until gate_1_signals_inbox suspends
        async with db_session.begin():
            executor = PipelineExecutor(run_id)
            await executor.run(db_session)

    async with db_session.begin():
        run_obj = await repo.get_pipeline_run(db_session, run_id)
        assert run_obj.status == "awaiting_approval", (
            f"Expected awaiting_approval, got {run_obj.status}"
        )
        ns = run_obj.node_states
        assert ns.get("gate_1_signals_inbox", {}).get("status") == "suspended"

        # Approve gate_1
        ns["gate_1_signals_inbox"]["decision"] = "approved"
        ns["gate_1_signals_inbox"]["decided_at"] = "2026-05-22T00:00:00+00:00"
        ns["gate_1_signals_inbox"]["decided_by"] = "test@example.com"
        await repo.update_pipeline_run(db_session, run_id, node_states=ns, status="running")

    # Second pass: runs from gate_1 onwards until the initiation gate suspends
    with (
        patch(
            "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
            new=_mock_execute_agent_node,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with db_session.begin():
            executor2 = PipelineExecutor(run_id)
            await executor2.run(db_session)

    async with db_session.begin():
        run_obj = await repo.get_pipeline_run(db_session, run_id)
        ns = run_obj.node_states
        assert ns.get("gate_campaign_initiation", {}).get("status") == "suspended", (
            f"Expected initiation gate suspended; got {ns.get('gate_campaign_initiation')}"
        )

        # Approve initiation gate
        ns["gate_campaign_initiation"]["decision"] = "approved"
        ns["gate_campaign_initiation"]["decided_at"] = "2026-05-22T00:30:00+00:00"
        ns["gate_campaign_initiation"]["decided_by"] = "test@example.com"
        await repo.update_pipeline_run(db_session, run_id, node_states=ns, status="running")

    # Third pass: runs until gate_2 suspends
    with (
        patch(
            "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
            new=_mock_execute_agent_node,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with db_session.begin():
            executor3 = PipelineExecutor(run_id)
            await executor3.run(db_session)

    async with db_session.begin():
        run_obj = await repo.get_pipeline_run(db_session, run_id)
        ns = run_obj.node_states
        assert ns.get("gate_2_approval_drawer", {}).get("status") == "suspended", (
            f"Expected gate_2 suspended; got {ns.get('gate_2_approval_drawer')}"
        )

        ns["gate_2_approval_drawer"]["decision"] = "approved"
        ns["gate_2_approval_drawer"]["decided_at"] = "2026-05-22T01:00:00+00:00"
        ns["gate_2_approval_drawer"]["decided_by"] = "test@example.com"
        await repo.update_pipeline_run(db_session, run_id, node_states=ns, status="running")

    # Fourth pass: completes remaining nodes
    with (
        patch(
            "artemis.pipelines.node_executors.agent_executor.execute_agent_node",
            new=_mock_execute_agent_node,
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._get_slack_token",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "artemis.pipelines.node_executors.human_gate_executor._schedule_timeout",
            return_value=None,
        ),
    ):
        async with db_session.begin():
            executor4 = PipelineExecutor(run_id)
            await executor4.run(db_session)

    async with db_session.begin():
        final = await repo.get_pipeline_run(db_session, run_id)
        assert final.status == "succeeded", (
            f"Expected succeeded; got {final.status}; error={final.error_message}"
        )
        ns = final.node_states
        total_nodes = len([k for k in ns if not k.startswith("_")])
        assert total_nodes == 19, (
            f"Expected 19 nodes in state, got {total_nodes}: {list(ns.keys())}"
        )
        assert ns["trigger_scheduled"]["status"] == "succeeded"
        assert ns["gate_1_signals_inbox"]["status"] == "succeeded"
        assert ns["gate_campaign_initiation"]["status"] == "succeeded"
        assert ns["gate_2_approval_drawer"]["status"] == "succeeded"
        assert ns["deliverable_outreach_email"]["status"] == "succeeded"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_mock_agent_run(agent_id: str) -> Any:
    """Create a mock AgentRun that looks succeeded (for internal use)."""
    run = MagicMock()
    run.id = 1  # BigInteger PK
    run.run_id = f"mock-run-{agent_id[:8]}"
    run.status = "completed"
    run.error = None
    run.cost_input_tokens = 100
    run.cost_output_tokens = 50
    run.agent_id = agent_id
    return run


def _mock_agent_node_result(agent_id: str = "mock") -> dict[str, Any]:
    """Node state dict returned by execute_agent_node (for patching)."""
    return {
        "status": "succeeded",
        "output_summary": f"Mocked agent '{agent_id}' completed",
        "cost_usd": 0.001,
        "agent_run_id": f"mock-run-{agent_id[:8]}",
    }
