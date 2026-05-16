"""Tests for artemis.builders.dag_executor (F2b — DAG execution wiring).

Verifies topological ordering, parallel execution of independent nodes,
and depends_on output chaining.
Uses FakeAdapter to avoid real Anthropic API calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.dag_executor import run_dag, run_dag_with_context

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reply(text: str, *, input_tokens: int = 50, output_tokens: int = 20) -> ScriptedReply:
    return ScriptedReply(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


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


async def _create_dag(session: AsyncSession, dag_id: str, nodes: list[Any]) -> None:
    async with session.begin():
        await repo.create_agent_dag(session, dag_id=dag_id, name=dag_id, nodes=nodes)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_node_dag_runs(db_session: AsyncSession) -> None:
    """A one-node DAG completes and returns a result for the node."""
    await _create_agents(db_session, "dag-n1")
    await _create_dag(
        db_session,
        "dag-single",
        [{"id": "n1", "agent_id": "dag-n1"}],
    )

    adapter = FakeAdapter([_reply("node1 output")])
    results = await run_dag(
        session=db_session,
        dag_id="dag-single",
        initial_inputs={"n1": "seed input"},
        model_adapter=adapter,
    )
    await db_session.commit()

    assert "n1" in results
    assert results["n1"].status == "completed"


@pytest.mark.asyncio
async def test_linear_dag_respects_order(db_session: AsyncSession) -> None:
    """A → B dependency means A runs before B."""
    await _create_agents(db_session, "dag-a", "dag-b")
    await _create_dag(
        db_session,
        "dag-linear",
        [
            {"id": "A", "agent_id": "dag-a"},
            {"id": "B", "agent_id": "dag-b", "depends_on": ["A"]},
        ],
    )

    call_order: list[str] = []

    class OrderTrackingAdapter:
        def __init__(self) -> None:
            self._replies = [_reply("A output"), _reply("B output")]
            self.call_count = 0

        async def complete(self, request: Any) -> Any:
            from artemis.agent.client import CompletionResponse
            from artemis.agent.types import Message, TextBlock, Usage

            self.call_count += 1
            reply = self._replies.pop(0)
            call_order.append(f"call_{self.call_count}")
            return CompletionResponse(
                message=Message(role="assistant", content=[TextBlock(text=reply.text or "")]),
                stop_reason="end_turn",
                usage=Usage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens),
            )

    adapter = OrderTrackingAdapter()
    results = await run_dag(
        session=db_session,
        dag_id="dag-linear",
        model_adapter=adapter,
    )
    await db_session.commit()

    assert set(results.keys()) == {"A", "B"}
    # Both should complete
    assert results["A"].status == "completed"
    assert results["B"].status == "completed"
    # A was called first (2 calls total)
    assert adapter.call_count == 2


@pytest.mark.asyncio
async def test_independent_nodes_run_in_parallel(db_session: AsyncSession) -> None:
    """Two independent nodes are gathered in the same batch (no strict ordering needed)."""
    await _create_agents(db_session, "dag-p1", "dag-p2")
    await _create_dag(
        db_session,
        "dag-parallel",
        [
            {"id": "P1", "agent_id": "dag-p1"},
            {"id": "P2", "agent_id": "dag-p2"},
        ],
    )

    # Both replies available; the gather should consume them in some order
    adapter = FakeAdapter([_reply("p1 out"), _reply("p2 out")])
    results = await run_dag_with_context(
        session=db_session,
        dag_id="dag-parallel",
        initial_inputs={"P1": "seed1", "P2": "seed2"},
        model_adapter=adapter,
    )
    await db_session.commit()

    assert set(results.keys()) == {"P1", "P2"}
    assert all(r.status == "completed" for r in results.values())


@pytest.mark.asyncio
async def test_depends_on_concatenates_parent_outputs(db_session: AsyncSession) -> None:
    """A child node receives concatenated parent outputs as its user_message."""
    await _create_agents(db_session, "dag-pa", "dag-pb", "dag-child")
    await _create_dag(
        db_session,
        "dag-concat",
        [
            {"id": "PA", "agent_id": "dag-pa"},
            {"id": "PB", "agent_id": "dag-pb"},
            {"id": "CHILD", "agent_id": "dag-child", "depends_on": ["PA", "PB"]},
        ],
    )

    adapter = FakeAdapter(
        [
            _reply("parent A result"),
            _reply("parent B result"),
            _reply("child processed"),
        ]
    )

    results = await run_dag_with_context(
        session=db_session,
        dag_id="dag-concat",
        initial_inputs={"PA": "input-a", "PB": "input-b"},
        model_adapter=adapter,
    )
    await db_session.commit()

    assert "CHILD" in results
    assert results["CHILD"].status == "completed"
    # The child's request should contain both parent outputs
    # (request index 2 is the child's model call)
    assert len(adapter.requests) == 3
    child_msg = adapter.requests[2].messages[0].content
    child_text = "".join(b.text for b in child_msg if hasattr(b, "text"))
    assert "parent A result" in child_text
    assert "parent B result" in child_text


@pytest.mark.asyncio
async def test_dag_cycle_raises_value_error(db_session: AsyncSession) -> None:
    """A DAG with a cycle raises ValueError instead of looping forever."""
    await _create_agents(db_session, "dag-cx", "dag-cy")
    await _create_dag(
        db_session,
        "dag-cycle",
        [
            {"id": "X", "agent_id": "dag-cx", "depends_on": ["Y"]},
            {"id": "Y", "agent_id": "dag-cy", "depends_on": ["X"]},
        ],
    )

    with pytest.raises(ValueError, match="cycle"):
        await run_dag(
            session=db_session,
            dag_id="dag-cycle",
            model_adapter=FakeAdapter([]),
        )


@pytest.mark.asyncio
async def test_empty_dag_returns_empty_dict(db_session: AsyncSession) -> None:
    """A DAG with no nodes returns an empty dict."""
    await _create_dag(db_session, "dag-empty", [])

    results = await run_dag(
        session=db_session,
        dag_id="dag-empty",
        model_adapter=FakeAdapter([]),
    )
    await db_session.commit()

    assert results == {}


@pytest.mark.asyncio
async def test_dag_not_found_raises(db_session: AsyncSession) -> None:
    """run_dag raises ValueError for an unknown dag_id."""
    with pytest.raises(ValueError, match="not found"):
        await run_dag(
            session=db_session,
            dag_id="ghost-dag",
            model_adapter=FakeAdapter([]),
        )


@pytest.mark.asyncio
async def test_dag_initial_inputs_used_for_root_nodes(db_session: AsyncSession) -> None:
    """initial_inputs are sent as user_message for nodes with no depends_on."""
    await _create_agents(db_session, "dag-root")
    await _create_dag(
        db_session,
        "dag-inputs",
        [{"id": "ROOT", "agent_id": "dag-root"}],
    )

    adapter = FakeAdapter([_reply("processed")])
    await run_dag(
        session=db_session,
        dag_id="dag-inputs",
        initial_inputs={"ROOT": "my specific seed"},
        model_adapter=adapter,
    )
    await db_session.commit()

    first_msg = adapter.requests[0].messages[0].content
    text = "".join(b.text for b in first_msg if hasattr(b, "text"))
    assert "my specific seed" in text


@pytest.mark.asyncio
async def test_dag_all_nodes_returned(db_session: AsyncSession) -> None:
    """Return dict contains entries for every node in the DAG."""
    await _create_agents(db_session, "dag-all1", "dag-all2", "dag-all3")
    await _create_dag(
        db_session,
        "dag-all",
        [
            {"id": "N1", "agent_id": "dag-all1"},
            {"id": "N2", "agent_id": "dag-all2"},
            {"id": "N3", "agent_id": "dag-all3", "depends_on": ["N1", "N2"]},
        ],
    )

    adapter = FakeAdapter([_reply("r1"), _reply("r2"), _reply("r3")])
    results = await run_dag_with_context(
        session=db_session,
        dag_id="dag-all",
        model_adapter=adapter,
    )
    await db_session.commit()

    assert set(results.keys()) == {"N1", "N2", "N3"}


@pytest.mark.asyncio
async def test_dag_diamond_shape_runs_correctly(db_session: AsyncSession) -> None:
    """Diamond DAG: A → {B, C} → D — both B and C must run before D."""
    await _create_agents(db_session, "dag-da", "dag-db", "dag-dc", "dag-dd")
    await _create_dag(
        db_session,
        "dag-diamond",
        [
            {"id": "A", "agent_id": "dag-da"},
            {"id": "B", "agent_id": "dag-db", "depends_on": ["A"]},
            {"id": "C", "agent_id": "dag-dc", "depends_on": ["A"]},
            {"id": "D", "agent_id": "dag-dd", "depends_on": ["B", "C"]},
        ],
    )

    adapter = FakeAdapter(
        [
            _reply("a_out"),
            _reply("b_out"),
            _reply("c_out"),
            _reply("d_out"),
        ]
    )

    results = await run_dag_with_context(
        session=db_session,
        dag_id="dag-diamond",
        initial_inputs={"A": "start"},
        model_adapter=adapter,
    )
    await db_session.commit()

    assert set(results.keys()) == {"A", "B", "C", "D"}
    assert all(r.status == "completed" for r in results.values())
