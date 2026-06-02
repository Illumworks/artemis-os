"""CC14 — Tests: summarize_async fires AFTER the agent_run row is committed.

Three tests:

1. Real-DB FK visibility: after run_agent() completes (which now commits the
   agent_run row before firing summarize_async), calling summarize() on the
   resulting snapshot succeeds — the FK target (agent_runs.id) is visible to
   the summarizer's separate session and the INSERT succeeds.

2. Commit-before-summarize ordering: a spy on session.commit() and
   summarize_async() confirms that commit() is called before summarize_async()
   is scheduled inside run_agent().

3. CC10/CC11/CC13 regression guard: the modules for those tests remain
   importable (no import-level breakage from CC14 changes).
"""

from __future__ import annotations

import contextlib
import json
import logging
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize
from artemis.builders import repository as builders_repo
from artemis.builders.models import AgentRunTrajectorySummary

# ── Helper ────────────────────────────────────────────────────────────────────


async def _make_agent(session: AsyncSession, agent_id: str) -> None:
    """Insert a minimal agent row (idempotent — skip if already present)."""
    with contextlib.suppress(Exception):
        await builders_repo.create_agent(
            session,
            agent_id=agent_id,
            name=f"CC14 Test Agent ({agent_id})",
            goal="Verify post-commit trajectory fix",
            system_prompt="You are a test agent.",
            tools=[],
            model="claude-sonnet-4-6",
        )


# ── Test 1: FK target visible — summarize INSERT succeeds ────────────────────


@pytest.mark.asyncio
async def test_fk_visible_after_run_agent_commit(db_session: AsyncSession) -> None:
    """After run_agent() completes, the agent_run row IS committed (CC14 fix).

    This test proves the FK target is visible to a SEPARATE session opened by
    summarize(). Before CC14, the summarizer's INSERT failed with:
      ForeignKeyViolationError: Key (run_id)=(N) is not present in table "agent_runs"

    We call run_agent() with a FakeAdapter (no real LLM), then open a new session
    and call summarize() on the snapshot.  If the FK target is visible, the
    INSERT succeeds and we can query the row.
    """
    from sqlalchemy import select

    agent_id = "cc14-fk-visibility-agent"
    await _make_agent(db_session, agent_id)
    await db_session.commit()

    # Agent adapter: returns a trivial text response.
    agent_adapter = FakeAdapter(
        [ScriptedReply(text="CC14 test response", input_tokens=50, output_tokens=20)]
    )

    from artemis.builders.executor import run_agent

    run = await run_agent(
        session=db_session,
        agent_id=agent_id,
        model_adapter=agent_adapter,
    )
    # run_agent now commits the agent_run row inside (CC14 fix).
    # The db_session transaction is now committed up to that point.

    # Build snapshot from the returned run object.
    snapshot = AgentRunSnapshot(
        run_id=run.run_id,
        run_pk=run.id,
        agent_id=run.agent_id,
        status=run.status,
        user_message=run.user_message,
        error=run.error,
    )

    # Now call summarize() with a separate session to prove FK visibility.
    # This is the scenario that used to fail with ForeignKeyViolationError.
    summary_json = json.dumps(
        {
            "what_worked": "The agent executed the test task without errors.",
            "what_stalled": "No stalls observed.",
            "what_was_missing": "No missing capabilities.",
        }
    )
    summary_adapter = FakeAdapter([ScriptedReply(text=summary_json)])

    # Open a completely separate session — simulates what summarize_async does.
    import artemis.db as _db

    async with _db.SessionLocal() as separate_session:
        # This must NOT raise ForeignKeyViolationError (the CC14 regression).
        await summarize(snapshot, adapter=summary_adapter, db_session=separate_session)

    # Verify the row landed.
    await db_session.rollback()  # clear any stale state
    async with _db.SessionLocal() as verify_session:
        result = await verify_session.execute(
            select(AgentRunTrajectorySummary).where(
                AgentRunTrajectorySummary.run_id == snapshot.run_pk
            )
        )
        row = result.scalar_one_or_none()

    assert row is not None, (
        f"No trajectory summary row found for run_pk={snapshot.run_pk}. "
        "FK target was not visible — CC14 fix did not commit the agent_run row."
    )
    assert row.what_worked is not None, "what_worked is null — summary not written"


# ── Test 2: commit() happens before summarize_async() ────────────────────────


@pytest.mark.asyncio
async def test_commit_before_summarize_async(db_session: AsyncSession) -> None:
    """Inside run_agent(), session.commit() is called before summarize_async().

    We spy on both calls and record their order. The commit MUST precede the
    summarize_async scheduling so the FK target is durably visible before the
    background task's session opens.
    """
    agent_id = "cc14-ordering-agent"
    await _make_agent(db_session, agent_id)
    await db_session.commit()

    agent_adapter = FakeAdapter(
        [ScriptedReply(text="ordering test response", input_tokens=30, output_tokens=15)]
    )

    call_order: list[str] = []

    # Patch session.commit to record the call.
    original_commit = db_session.commit

    async def spy_commit() -> None:
        call_order.append("commit")
        await original_commit()

    # Patch summarize_async to record the call (and suppress background I/O).
    async def spy_summarize_async(snapshot: AgentRunSnapshot) -> None:
        call_order.append("summarize_async")

    from artemis.builders.executor import run_agent

    db_session.commit = spy_commit  # type: ignore[method-assign]

    # summarize_async is imported lazily inside run_agent via:
    #   from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize_async
    # so we patch at the module where it lives.
    with patch(
        "artemis.builder.trajectory_summarizer.summarize_async",
        side_effect=spy_summarize_async,
    ):
        await run_agent(
            session=db_session,
            agent_id=agent_id,
            model_adapter=agent_adapter,
        )

    # Restore
    db_session.commit = original_commit  # type: ignore[method-assign]

    assert "commit" in call_order, "session.commit() was never called inside run_agent()"
    assert "summarize_async" in call_order, "summarize_async() was never called inside run_agent()"

    commit_idx = call_order.index("commit")
    summarize_idx = call_order.index("summarize_async")
    assert commit_idx < summarize_idx, (
        f"commit() (pos {commit_idx}) must happen BEFORE summarize_async() "
        f"(pos {summarize_idx}). Got order: {call_order}. "
        "CC14 fix is not in place."
    )


# ── Test 3: CC10/CC11/CC13 regression guard ──────────────────────────────────


def test_cc10_cc11_cc13_still_importable() -> None:
    """Prior trajectory test modules remain importable after CC14 changes.

    We import the key symbols from each module. If CC14 broke any of the prior
    layers (GC retention, brace fix, snapshot no-lookup) this will fail.
    """
    # CC10: GC retention
    from artemis.builder import trajectory_summarizer as ts  # noqa: F401

    assert hasattr(ts, "_BACKGROUND_TASKS"), "CC10: _BACKGROUND_TASKS set missing"
    assert hasattr(ts, "summarize_async"), "CC10: summarize_async missing"

    # CC11: brace fix
    from artemis.builder.trajectory_summarizer import _TRAJECTORY_PROMPT

    sample = json.dumps({"x": 1})
    result = _TRAJECTORY_PROMPT.format(run_data=sample)
    assert sample in result, "CC11: prompt format substitution broken"
    assert '"what_worked"' in result, "CC11: literal braces in example lost"

    # CC13: snapshot dataclass exists with all required fields
    fields = {f.name for f in ts.AgentRunSnapshot.__dataclass_fields__.values()}
    required = {"run_id", "run_pk", "agent_id", "status", "user_message", "error"}
    assert required <= fields, f"CC13: AgentRunSnapshot missing fields: {required - fields}"


# ── Test 4: Part D — info log appears on successful insert ────────────────────


@pytest.mark.asyncio
async def test_success_info_log_emitted(
    db_session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    """After a successful summarize(), the info log includes run_pk and field previews.

    This guards the Part D defensive logging requirement: grep-able success
    lines in logs for smoke tests.
    """
    import uuid as _uuid

    from artemis.builders.models import AgentRun

    agent_id = "cc14-log-agent"
    await _make_agent(db_session, agent_id)
    await db_session.commit()

    # Insert a committed agent_run directly so we control the run_pk.
    run_uuid = str(_uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id=agent_id,
        status="completed",
        user_message="Check the log output.",
        error=None,
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.refresh(run)
    await db_session.commit()

    snapshot = AgentRunSnapshot(
        run_id=run.run_id,
        run_pk=run.id,
        agent_id=run.agent_id,
        status=run.status,
        user_message=run.user_message,
        error=run.error,
    )

    summary_json = json.dumps(
        {
            "what_worked": "Signal fetch tool returned 3 results.",
            "what_stalled": "Qualifier loop ran twice without progress.",
            "what_was_missing": "A rate-limit retry tool was absent.",
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=summary_json)])

    with caplog.at_level(logging.INFO, logger="artemis.builder.trajectory_summarizer"):
        await summarize(snapshot, adapter=adapter, db_session=db_session)

    log_lines = [r.message for r in caplog.records if "trajectory_summarizer" in r.name]
    assert any("summarized" in line for line in log_lines), (
        f"Expected a 'summarized' info log from trajectory_summarizer, got: {log_lines}"
    )
    # Verify run_pk appears in the log message.
    assert any(str(snapshot.run_pk) in line for line in log_lines), (
        f"run_pk={snapshot.run_pk} not in any trajectory_summarizer log: {log_lines}"
    )
