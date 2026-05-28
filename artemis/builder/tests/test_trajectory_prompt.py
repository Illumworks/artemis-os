"""CC11 — Tests for _TRAJECTORY_PROMPT brace fix.

Two tests:
1. Unit: _TRAJECTORY_PROMPT.format(run_data=...) does NOT raise KeyError;
   result contains both the literal JSON example and the substituted run_data.
   This is the regression guard that should have existed from day one.

2. Integration (sync path): summarize() called with a mocked adapter that
   returns valid JSON → agent_run_trajectory_summaries row is written with
   non-null what_worked / what_stalled / what_was_missing fields.
   (The real async path proof is Lead's post-merge bundled smoke of CC8+CC9+CC10+CC11.)
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.trajectory_summarizer import _TRAJECTORY_PROMPT
from artemis.builders import repository as repo
from artemis.builders.models import AgentRunTrajectorySummary

# ── Unit test: prompt template formats without KeyError ────────────────────────


def test_trajectory_prompt_format_no_key_error() -> None:
    """_TRAJECTORY_PROMPT.format(run_data=...) must not raise KeyError.

    This is the regression guard. Before CC11, the literal braces in the
    JSON example caused str.format() to interpret "what_worked" etc. as
    placeholder names → KeyError swallowed by _safe_summarize.
    """
    sample_run_data = json.dumps({"a": 1, "b": "hello"})
    result = _TRAJECTORY_PROMPT.format(run_data=sample_run_data)

    # The substituted run_data must appear in the output
    assert sample_run_data in result, "run_data substitution missing from formatted prompt"

    # The literal JSON example must appear (braces now literal, not substituted away)
    assert '"what_worked"' in result
    assert '"what_stalled"' in result
    assert '"what_was_missing"' in result

    # The formatted result must be a non-empty string
    assert isinstance(result, str)
    assert len(result) > 0


# ── Integration test: summarize() sync path writes row with non-null fields ───


@pytest.mark.asyncio
async def test_summarize_sync_writes_non_null_fields(db_session: AsyncSession) -> None:
    """summarize() with a mocked adapter → trajectory row with non-null fields.

    This proves the prompt-format path completes end-to-end: the KeyError no
    longer fires, the LLM call returns, and the summary is persisted.

    Note: the REAL async path (summarize_async + event-loop drain) proof
    requires CC8+CC9+CC10+CC11 all merged together. Lead runs that bundled
    smoke post-merge. This test covers the sync path on this branch alone.
    """
    # 1. Create an agent + agent_run so summarize() has a real row to load.
    async with db_session.begin():
        await repo.create_agent(
            db_session,
            agent_id="cc11-test-agent",
            name="CC11 Test Agent",
            goal="Prove brace fix",
            system_prompt="You are a test agent.",
            tools=[],
            model="claude-sonnet-4-6",
        )

    from artemis.builders.executor import run_agent

    mocked_llm = FakeAdapter(
        [
            ScriptedReply(text="agent output text", input_tokens=100, output_tokens=50),
        ]
    )

    run = await run_agent(
        session=db_session,
        agent_id="cc11-test-agent",
        model_adapter=mocked_llm,
    )
    await db_session.commit()

    # 2. Build a fake adapter that returns valid JSON for the trajectory summarizer.
    summary_json = json.dumps(
        {
            "what_worked": "The agent completed the test task successfully.",
            "what_stalled": "No stalls were observed in this run.",
            "what_was_missing": "No missing capabilities identified.",
        }
    )
    summary_adapter = FakeAdapter([ScriptedReply(text=summary_json)])

    # 3. Call summarize() synchronously, injecting both adapter and session.
    from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize

    snapshot = AgentRunSnapshot(
        run_id=run.run_id,
        run_pk=run.id,
        agent_id=run.agent_id,
        status=run.status,
        user_message=run.user_message,
        error=run.error,
    )
    await summarize(snapshot, adapter=summary_adapter, db_session=db_session)
    await db_session.commit()

    # 4. Verify the row was written with non-null fields.
    from sqlalchemy import select

    result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run.id)
    )
    row = result.scalar_one_or_none()

    assert row is not None, "No trajectory summary row was written — summarize() failed silently"
    assert row.what_worked is not None, "what_worked is null — JSON parse or prompt-format failed"
    assert row.what_stalled is not None, "what_stalled is null"
    assert row.what_was_missing is not None, "what_was_missing is null"

    assert "successfully" in row.what_worked
    assert "stalls" in row.what_stalled
    assert "missing" in row.what_was_missing


# ── Smoke: existing wire regression tests still importable (CC10 guard) ────────


def test_trajectory_prompt_is_str() -> None:
    """Sanity: _TRAJECTORY_PROMPT is a non-empty string (module loads cleanly)."""
    assert isinstance(_TRAJECTORY_PROMPT, str)
    assert len(_TRAJECTORY_PROMPT) > 0
    assert "{run_data}" in _TRAJECTORY_PROMPT
