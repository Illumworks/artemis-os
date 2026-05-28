"""CC13 — Tests: trajectory summarizer passes data in, no AgentRun DB lookup.

Four tests:
1. No AgentRun SELECT is issued — the summarizer uses snapshot data directly.
2. The LLM is called with snapshot fields embedded in the prompt.
3. The trajectory_summary row is written with the correct agent_run_id (run_pk).
4. Regression: invalid LLM JSON → fallback row with all-null fields (no data loss).

The CC10 (GC-retention) and CC11 (brace-escape) regression tests live in their
own files and are assumed to pass; we guard against import-level breakage only.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize
from artemis.builders import repository as builders_repo
from artemis.builders.models import AgentRunTrajectorySummary

# ── Helper: build a committed agent + run row ─────────────────────────────────


async def _make_run(session: AsyncSession, agent_id: str = "cc13-agent") -> tuple[str, int]:
    """Insert minimal agent + agent_run rows. Returns (run_id str, run.id int)."""
    import uuid as _uuid

    from artemis.builders.models import AgentRun

    await builders_repo.create_agent(
        session,
        agent_id=agent_id,
        name="CC13 Test Agent",
        goal="Test no-lookup path",
        system_prompt="You are a test agent.",
        tools=[],
        model="claude-sonnet-4-6",
    )
    run_uuid = str(_uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id=agent_id,
        status="completed",
        user_message="Run the marketing pipeline.",
        error=None,
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    await session.commit()
    return run_uuid, run.id


# ── Test 1: No AgentRun SELECT is issued ─────────────────────────────────────


@pytest.mark.asyncio
async def test_no_agent_run_select(db_session: AsyncSession) -> None:
    """summarize(snapshot) must NOT execute a SELECT on agent_runs.

    We patch session.execute on the inner session object (passed as db_session)
    and verify that no call to it involves an AgentRun SELECT.  The
    idempotency check (get_trajectory_summary) does query
    agent_run_trajectory_summaries — that is allowed.  Any query touching
    'agent_runs' (the table backing AgentRun) is not allowed.
    """
    run_uuid, run_pk = await _make_run(db_session, agent_id="cc13-no-select-agent")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="cc13-no-select-agent",
        status="completed",
        user_message="test message",
        error=None,
    )

    valid_json = json.dumps(
        {
            "what_worked": "Search tool returned results.",
            "what_stalled": "No stalls.",
            "what_was_missing": "Nothing missing.",
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=valid_json)])

    # Track execute calls on the db_session.
    original_execute = db_session.execute
    execute_calls: list[str] = []

    async def spy_execute(stmt, *args, **kwargs):  # type: ignore[no-untyped-def]
        # Capture the compiled SQL string (lowercased) for assertion.
        try:
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": False})).lower()
        except Exception:
            compiled = str(stmt).lower()
        execute_calls.append(compiled)
        return await original_execute(stmt, *args, **kwargs)

    db_session.execute = spy_execute  # type: ignore[assignment]

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    # None of the execute calls should touch agent_runs table.
    agent_run_selects = [c for c in execute_calls if "from agent_runs" in c]
    assert agent_run_selects == [], (
        f"summarize() issued a SELECT on agent_runs — lookup not eliminated. "
        f"Queries: {agent_run_selects}"
    )


# ── Test 2: LLM prompt contains snapshot fields ───────────────────────────────


@pytest.mark.asyncio
async def test_llm_prompt_contains_snapshot_fields(db_session: AsyncSession) -> None:
    """The LLM is called with snapshot data embedded in the prompt."""
    run_uuid, run_pk = await _make_run(db_session, agent_id="cc13-prompt-agent")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="cc13-prompt-agent",
        status="failed",
        user_message="What is our MQL velocity?",
        error="TimeoutError: scout timed out after 300s",
    )

    valid_json = json.dumps(
        {
            "what_worked": "Qualifier ran to completion.",
            "what_stalled": "Scout timed out.",
            "what_was_missing": "Shorter timeout config.",
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=valid_json)])

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    # Inspect what the adapter received.
    assert len(adapter.requests) == 1, "Expected exactly one LLM call"
    request = adapter.requests[0]
    # The prompt is the first user message.
    assert len(request.messages) >= 1
    first_msg = request.messages[0]
    prompt_text = first_msg.content[0].text  # type: ignore[union-attr]

    assert snapshot.run_id in prompt_text, "run_id UUID not in prompt"
    assert "cc13-prompt-agent" in prompt_text, "agent_id not in prompt"
    assert "failed" in prompt_text, "status not in prompt"
    assert "What is our MQL velocity?" in prompt_text, "user_message not in prompt"
    assert "TimeoutError" in prompt_text, "error not in prompt"


# ── Test 3: summary row uses run_pk as FK ─────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_row_uses_run_pk(db_session: AsyncSession) -> None:
    """The written trajectory_summary row has agent_run_id == snapshot.run_pk."""
    from sqlalchemy import select

    run_uuid, run_pk = await _make_run(db_session, agent_id="cc13-fk-agent")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="cc13-fk-agent",
        status="completed",
        user_message="Fetch competitor data.",
        error=None,
    )

    valid_json = json.dumps(
        {
            "what_worked": "Competitor data was fetched.",
            "what_stalled": None,
            "what_was_missing": None,
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=valid_json)])

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    row = result.scalar_one_or_none()

    assert row is not None, f"No trajectory summary row found for run_pk={run_pk}"
    assert row.run_id == run_pk, f"FK mismatch: row.run_id={row.run_id} != run_pk={run_pk}"
    assert row.what_worked is not None


# ── Test 4: invalid JSON → fallback row with all-null fields ──────────────────


@pytest.mark.asyncio
async def test_invalid_json_fallback_produces_null_row(db_session: AsyncSession) -> None:
    """When the LLM returns non-JSON, a row with all-null summary fields is still written.

    This defensive behavior (don't lose the row even on parse failure) must be
    preserved through the CC13 refactor.
    """
    from sqlalchemy import select

    run_uuid, run_pk = await _make_run(db_session, agent_id="cc13-json-fallback-agent")

    snapshot = AgentRunSnapshot(
        run_id=run_uuid,
        run_pk=run_pk,
        agent_id="cc13-json-fallback-agent",
        status="completed",
        user_message="Summarize MQL trends.",
        error=None,
    )

    # LLM returns unparseable text.
    adapter = FakeAdapter([ScriptedReply(text="Sorry, I cannot summarize this right now.")])

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    result = await db_session.execute(
        select(AgentRunTrajectorySummary).where(AgentRunTrajectorySummary.run_id == run_pk)
    )
    row = result.scalar_one_or_none()

    assert row is not None, (
        "No trajectory summary row was written after invalid JSON — the fallback path is broken"
    )
    assert row.what_worked is None, "what_worked should be null on fallback"
    assert row.what_stalled is None, "what_stalled should be null on fallback"
    assert row.what_was_missing is None, "what_was_missing should be null on fallback"
