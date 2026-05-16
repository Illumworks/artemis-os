"""Tests for artemis.builders.workflow_executor (F2b — workflow execution wiring).

Uses FakeAdapter to avoid real Anthropic API calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builders import repository as repo
from artemis.builders.workflow_executor import run_workflow

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake(*texts: str) -> FakeAdapter:
    """Create a FakeAdapter with one ScriptedReply per text."""
    return FakeAdapter([ScriptedReply(text=t) for t in texts])


async def _create_workflow(session: AsyncSession, workflow_id: str, steps: list[Any]) -> None:
    async with session.begin():
        await repo.create_workflow(
            session,
            workflow_id=workflow_id,
            name=f"WF {workflow_id}",
            steps=steps,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_step_workflow_completes(db_session: AsyncSession) -> None:
    """A one-step workflow runs and returns status='completed'."""
    await _create_workflow(db_session, "wf-single", [{"name": "step1", "prompt": "Do the thing."}])

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-single",
        model_adapter=fake("Step 1 done"),
    )
    await db_session.commit()

    assert run.status == "completed"
    assert run.current_step == 1
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_single_step_context_stored(db_session: AsyncSession) -> None:
    """Step response is stored in workflow context as step_0_response."""
    await _create_workflow(db_session, "wf-ctx", [{"name": "step1", "prompt": "Answer something."}])

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-ctx",
        model_adapter=fake("Context stored here"),
    )
    await db_session.commit()

    contexts = await repo.get_workflow_context(db_session, run.id)
    keys = [c.key for c in contexts]
    assert "step_0_response" in keys
    ctx_value = next(c.value for c in contexts if c.key == "step_0_response")
    assert "Context stored here" in ctx_value


@pytest.mark.asyncio
async def test_multi_step_workflow_runs_all_steps(db_session: AsyncSession) -> None:
    """A three-step workflow runs all steps and records current_step=3."""
    await _create_workflow(
        db_session,
        "wf-multi",
        [
            {"name": "step1", "prompt": "Step 1"},
            {"name": "step2", "prompt": "Step 2"},
            {"name": "step3", "prompt": "Step 3"},
        ],
    )

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-multi",
        model_adapter=fake("R1", "R2", "R3"),
    )
    await db_session.commit()

    assert run.status == "completed"
    assert run.current_step == 3

    contexts = await repo.get_workflow_context(db_session, run.id)
    keys = {c.key for c in contexts}
    assert {"step_0_response", "step_1_response", "step_2_response"}.issubset(keys)


@pytest.mark.asyncio
async def test_multi_step_cost_accumulated(db_session: AsyncSession) -> None:
    """Total cost is accumulated across all steps."""
    await _create_workflow(
        db_session,
        "wf-cost",
        [{"name": "s1", "prompt": "p1"}, {"name": "s2", "prompt": "p2"}],
    )

    adapter = FakeAdapter(
        [
            ScriptedReply(text="R1", input_tokens=300, output_tokens=100),
            ScriptedReply(text="R2", input_tokens=200, output_tokens=50),
        ]
    )

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-cost",
        model_adapter=adapter,
    )
    await db_session.commit()

    # Cost should be non-zero
    assert run.total_cost_usd > 0


@pytest.mark.asyncio
async def test_on_failure_fail_propagates(db_session: AsyncSession) -> None:
    """on_failure='fail' (default) halts the workflow on step failure."""

    class BrokenAdapter:
        async def complete(self, _request: Any) -> Any:
            raise RuntimeError("step exploded")

    await _create_workflow(
        db_session,
        "wf-fail",
        [{"name": "step1", "prompt": "Blow up"}, {"name": "step2", "prompt": "Never reached"}],
    )

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-fail",
        model_adapter=BrokenAdapter(),
    )
    await db_session.commit()

    assert run.status == "failed"
    # Should not have advanced past step 0
    assert run.current_step < 2


@pytest.mark.asyncio
async def test_on_failure_continue_skips_step(db_session: AsyncSession) -> None:
    """on_failure='continue' lets subsequent steps run even when a step fails."""

    class BrokenThenOkAdapter:
        def __init__(self) -> None:
            self._call = 0

        async def complete(self, _request: Any) -> Any:
            self._call += 1
            if self._call == 1:
                raise RuntimeError("first step error")
            from artemis.agent.client import CompletionResponse
            from artemis.agent.types import Message, TextBlock, Usage

            return CompletionResponse(
                message=Message(role="assistant", content=[TextBlock(text="step2 ok")]),
                stop_reason="end_turn",
                usage=Usage(input_tokens=10, output_tokens=5),
            )

    await _create_workflow(
        db_session,
        "wf-continue",
        [
            {"name": "step1", "prompt": "Fail here", "on_failure": "continue"},
            {"name": "step2", "prompt": "Should run"},
        ],
    )

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-continue",
        model_adapter=BrokenThenOkAdapter(),
    )
    await db_session.commit()

    # Workflow completed because step1 had on_failure='continue'
    assert run.status == "completed"
    assert run.current_step == 2


@pytest.mark.asyncio
async def test_empty_steps_workflow_completes(db_session: AsyncSession) -> None:
    """A workflow with no steps completes immediately with cost=0."""
    await _create_workflow(db_session, "wf-empty", [])

    run = await run_workflow(
        session=db_session,
        workflow_id="wf-empty",
        model_adapter=fake(),  # no replies needed
    )
    await db_session.commit()

    assert run.status == "completed"
    assert run.current_step == 0
    assert run.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_workflow_not_found_raises(db_session: AsyncSession) -> None:
    """run_workflow raises ValueError for unknown workflow_id."""
    with pytest.raises(ValueError, match="not found"):
        await run_workflow(
            session=db_session,
            workflow_id="ghost-workflow",
            model_adapter=fake("nope"),
        )
