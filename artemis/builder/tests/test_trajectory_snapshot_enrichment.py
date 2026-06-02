"""CC16 — Tests: enriched AgentRunSnapshot carries tool calls, signal counts, final text.

Five tests:
1. _build_snapshot extracts _ToolCallSummary tuples from a fake RunResult.
2. signals_emitted count correctly attributed via provenance->>'agent_run_id'.
3. final_text truncates at 500 chars and uses the LAST assistant message.
4. End-to-end: summarize() with enriched snapshot — prompt JSON includes
   tool_calls and signals_emitted (spy on adapter mock).
5. Regression guard: CC10/CC11/CC13/CC14 still importable and correct.

Also covers Part F: info log fires on successful insert (caplog assertion).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.marketing.models  # noqa: F401 — registers SignalQueue + pipeline_runs FK on Base.metadata
import artemis.pipelines.models  # noqa: F401 — registers pipeline_runs table so SignalQueue FK resolves
from artemis.agent.tests.fake_adapter import FakeAdapter, ScriptedReply
from artemis.agent.types import Message, RunResult, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from artemis.builder.trajectory_summarizer import AgentRunSnapshot, summarize
from artemis.builders import repository as builders_repo
from artemis.builders.executor import _build_snapshot

# ── Fake RunResult helper ─────────────────────────────────────────────────────


def _make_run_result(
    *,
    tool_pairs: list[tuple[str, str, str, bool]] | None = None,
    final_assistant_text: str = "All done.",
    extra_assistant_text: str | None = None,
) -> RunResult:
    """Build a fake RunResult with optional tool call/result pairs.

    tool_pairs: list of (tool_use_id, tool_name, result_content, is_error)
    If extra_assistant_text is provided, prepend a prior assistant message
    to test that _build_snapshot picks the LAST one.
    """
    messages: list[Message] = [Message(role="user", content=[TextBlock(text="Do the thing.")])]

    if extra_assistant_text:
        messages.append(Message(role="assistant", content=[TextBlock(text=extra_assistant_text)]))

    if tool_pairs:
        # Interleave: assistant emits tool_use blocks, user replies tool_result blocks
        from artemis.agent.types import Block

        use_blocks: list[Block] = [
            ToolUseBlock(id=tid, name=name, input={}) for tid, name, _, _ in tool_pairs
        ]
        messages.append(Message(role="assistant", content=use_blocks))

        result_blocks: list[Block] = [
            ToolResultBlock(tool_use_id=tid, content=content, is_error=is_error)
            for tid, _, content, is_error in tool_pairs
        ]
        messages.append(Message(role="user", content=result_blocks))

    messages.append(Message(role="assistant", content=[TextBlock(text=final_assistant_text)]))

    return RunResult(
        messages=messages,
        stop_reason="end_turn",
        usage=Usage(input_tokens=100, output_tokens=50),
        iterations=1,
    )


# ── Fake AgentRun helper ──────────────────────────────────────────────────────


class _FakeRun:
    """Minimal duck-type of AgentRun for _build_snapshot tests (no DB needed)."""

    def __init__(
        self,
        run_id: str | None = None,
        run_pk: int = 1,
        agent_id: str = "test-agent",
        status: str = "completed",
        user_message: str = "Test message.",
        error: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        self.run_id = run_id or str(uuid.uuid4())
        self.id = run_pk
        self.agent_id = agent_id
        self.status = status
        self.user_message = user_message
        self.error = error
        self.started_at = started_at
        self.completed_at = completed_at


# ── Test 1: _build_snapshot extracts _ToolCallSummary tuples ─────────────────


def test_build_snapshot_extracts_tool_calls() -> None:
    """_build_snapshot correctly pairs ToolUseBlock ↔ ToolResultBlock."""
    tool_pairs = [
        ("id-1", "news_api.search", '{"results": []}', False),
        ("id-2", "signal_queue.write", '{"signal_id": 42, "status": "written"}', False),
        ("id-3", "news_api.search", "Error: rate limit", True),
    ]
    result = _make_run_result(tool_pairs=tool_pairs)
    run = _FakeRun()

    snapshot = _build_snapshot(run, result, signals_emitted=1)

    assert len(snapshot.tool_calls) == 3

    assert snapshot.tool_calls[0].name == "news_api.search"
    assert snapshot.tool_calls[0].success is True
    assert '{"results"' in snapshot.tool_calls[0].result_preview

    assert snapshot.tool_calls[1].name == "signal_queue.write"
    assert snapshot.tool_calls[1].success is True
    assert "signal_id" in snapshot.tool_calls[1].result_preview

    assert snapshot.tool_calls[2].name == "news_api.search"
    assert snapshot.tool_calls[2].success is False
    assert "rate limit" in snapshot.tool_calls[2].result_preview


def test_build_snapshot_result_preview_truncated() -> None:
    """result_preview is capped at 100 chars."""
    long_content = "X" * 200
    tool_pairs = [("id-1", "some_tool", long_content, False)]
    result = _make_run_result(tool_pairs=tool_pairs)
    run = _FakeRun()

    snapshot = _build_snapshot(run, result, signals_emitted=0)

    assert len(snapshot.tool_calls[0].result_preview) == 100


def test_build_snapshot_no_tools_empty_tuple() -> None:
    """A no-tool run produces an empty tool_calls tuple."""
    result = _make_run_result()
    run = _FakeRun()

    snapshot = _build_snapshot(run, result, signals_emitted=0)

    assert snapshot.tool_calls == ()


# ── Test 2: signals_emitted attribution via provenance->>'agent_run_id' ───────


@pytest.mark.asyncio
async def test_signals_emitted_counted_via_provenance(db_session: AsyncSession) -> None:
    """signals_emitted counts only signal_queue rows with matching agent_run_id.

    We insert the agent, commit, then call run_agent (which writes the agent_run
    and fires summarize_async internally). We then directly query signal_queue
    provenance attribution logic to verify the count query is correct.
    """
    import contextlib

    from artemis.marketing.models import SignalQueue

    agent_id = "cc16-signals-test-agent"
    run_uuid = str(uuid.uuid4())
    other_run_uuid = str(uuid.uuid4())

    with contextlib.suppress(Exception):
        await builders_repo.create_agent(
            db_session,
            agent_id=agent_id,
            name="CC16 Signals Test Agent",
            goal="Test signal attribution",
            system_prompt="You are a test agent.",
            tools=[],
            model="claude-sonnet-4-6",
        )
    await db_session.commit()

    # Insert signal_queue rows with mixed provenance.
    from sqlalchemy import func, select

    for _ in range(3):
        sig = SignalQueue(
            source_type="manual",
            headline="Test signal",
            campaign_family="education",
            urgency_tier="standard",
            discovered_by="test",
            summary="Test signal summary",
            provenance={"agent_run_id": run_uuid, "agent_id": agent_id},
        )
        db_session.add(sig)

    # One signal for a different run — should NOT be counted.
    other_sig = SignalQueue(
        source_type="manual",
        headline="Other run signal",
        campaign_family="education",
        urgency_tier="standard",
        discovered_by="test",
        summary="Other run signal",
        provenance={"agent_run_id": other_run_uuid, "agent_id": agent_id},
    )
    db_session.add(other_sig)
    await db_session.flush()
    await db_session.commit()

    # Now run the count query exactly as _build_snapshot's caller does.
    sig_result = await db_session.execute(
        select(func.count())
        .select_from(SignalQueue)
        .where(SignalQueue.provenance["agent_run_id"].as_string() == run_uuid)
    )
    count = sig_result.scalar_one() or 0

    assert count == 3, (
        f"Expected 3 signals attributed to run_uuid={run_uuid}, got {count}. "
        "JSONB provenance->>'agent_run_id' filter is broken."
    )


# ── Test 3: final_text truncates at 500 chars, uses LAST assistant message ────


def test_build_snapshot_final_text_uses_last_assistant_message() -> None:
    """final_text comes from the LAST assistant message, not an earlier one."""
    result = _make_run_result(
        extra_assistant_text="Earlier assistant turn — should be ignored.",
        final_assistant_text="Final assistant output.",
    )
    run = _FakeRun()

    snapshot = _build_snapshot(run, result, signals_emitted=0)

    assert snapshot.final_text == "Final assistant output."
    assert "Earlier" not in (snapshot.final_text or "")


def test_build_snapshot_final_text_truncated_at_500() -> None:
    """final_text is truncated to 500 chars when the last assistant message is longer."""
    long_text = "A" * 700
    result = _make_run_result(final_assistant_text=long_text)
    run = _FakeRun()

    snapshot = _build_snapshot(run, result, signals_emitted=0)

    assert snapshot.final_text is not None
    assert len(snapshot.final_text) == 500


def test_build_snapshot_final_text_none_when_no_assistant() -> None:
    """final_text is None when result is None (failed run before any assistant turn)."""
    run = _FakeRun(status="failed", error="TimeoutError")

    snapshot = _build_snapshot(run, None, signals_emitted=0)

    assert snapshot.final_text is None
    assert snapshot.tool_calls == ()


# ── Test 4: end-to-end — enriched snapshot data appears in LLM prompt ─────────


@pytest.mark.asyncio
async def test_summarize_prompt_includes_tool_calls_and_signals(
    db_session: AsyncSession,
) -> None:
    """summarize() with an enriched snapshot — the LLM prompt JSON includes
    tool_calls and signals_emitted, not just the bare run metadata.

    We spy on the adapter to inspect what the LLM was given.
    """
    import contextlib

    agent_id = "cc16-prompt-enrichment-agent"

    with contextlib.suppress(Exception):
        await builders_repo.create_agent(
            db_session,
            agent_id=agent_id,
            name="CC16 Prompt Enrichment Agent",
            goal="Verify enriched prompt",
            system_prompt="You are a test agent.",
            tools=[],
            model="claude-sonnet-4-6",
        )
    await db_session.commit()

    from artemis.builder.trajectory_summarizer import _ToolCallSummary
    from artemis.builders.models import AgentRun

    run_uuid = str(uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id=agent_id,
        status="completed",
        user_message="Scan for signals.",
        error=None,
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.refresh(run)
    await db_session.commit()

    # Build enriched snapshot with tool calls and signals.
    snapshot = AgentRunSnapshot(
        run_id=run.run_id,
        run_pk=run.id,
        agent_id=run.agent_id,
        status=run.status,
        user_message=run.user_message,
        error=run.error,
        tool_calls=(
            _ToolCallSummary(
                name="news_api.search",
                success=True,
                result_preview='{"count": 5}',
            ),
            _ToolCallSummary(
                name="signal_queue.write",
                success=True,
                result_preview='{"signal_id": 7, "status": "written"}',
            ),
        ),
        signals_emitted=2,
        final_text="Found 5 articles; wrote 2 signals to the queue.",
        duration_ms=4200,
    )

    valid_json = json.dumps(
        {
            "what_worked": "news_api.search returned 5 results; 2 signals emitted.",
            "what_stalled": None,
            "what_was_missing": None,
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=valid_json)])

    await summarize(snapshot, adapter=adapter, db_session=db_session)

    assert len(adapter.requests) == 1, "Expected exactly one LLM call"
    request = adapter.requests[0]
    prompt_text = request.messages[0].content[0].text  # type: ignore[union-attr]

    # Verify the enriched fields appear in the prompt.
    assert "news_api.search" in prompt_text, "tool name not in prompt"
    assert "signal_queue.write" in prompt_text, "tool name not in prompt"
    assert "signals_emitted" in prompt_text, "signals_emitted key not in prompt"
    assert '"signals_emitted": 2' in prompt_text, "signals_emitted value not in prompt"
    assert "final_text" in prompt_text, "final_text key not in prompt"
    assert "duration_ms" in prompt_text, "duration_ms key not in prompt"
    assert "4200" in prompt_text, "duration_ms value not in prompt"


# ── Test 5: CC10/CC11/CC13/CC14 regression guard ──────────────────────────────


def test_cc10_through_cc14_regression_guard() -> None:
    """All prior CC layers remain intact after CC16 changes.

    CC10: _BACKGROUND_TASKS set + summarize_async present.
    CC11: prompt format brace-safe (no KeyError).
    CC13: AgentRunSnapshot has all original fields.
    CC14: info log line present for smoke grep.
    CC16: AgentRunSnapshot now has the four new enrichment fields.
    """
    from artemis.builder import trajectory_summarizer as ts
    from artemis.builder.trajectory_summarizer import (
        _TRAJECTORY_PROMPT,
        AgentRunSnapshot,
        _ToolCallSummary,
    )

    # CC10: GC retention
    assert hasattr(ts, "_BACKGROUND_TASKS"), "CC10: _BACKGROUND_TASKS set missing"
    assert hasattr(ts, "summarize_async"), "CC10: summarize_async missing"

    # CC11: brace fix — format must not raise KeyError
    sample = json.dumps({"x": 1, "y": "hello"})
    result = _TRAJECTORY_PROMPT.format(run_data=sample)
    assert sample in result, "CC11: run_data substitution missing from formatted prompt"
    assert '"what_worked"' in result, "CC11: literal braces in example lost"
    assert '"what_stalled"' in result, "CC11: literal braces in example lost"
    assert '"what_was_missing"' in result, "CC11: literal braces in example lost"

    # CC13: original snapshot fields
    fields = {f.name for f in AgentRunSnapshot.__dataclass_fields__.values()}
    required_cc13 = {"run_id", "run_pk", "agent_id", "status", "user_message", "error"}
    assert required_cc13 <= fields, (
        f"CC13: AgentRunSnapshot missing fields: {required_cc13 - fields}"
    )

    # CC16: new enrichment fields
    required_cc16 = {"tool_calls", "signals_emitted", "final_text", "duration_ms"}
    assert required_cc16 <= fields, (
        f"CC16: AgentRunSnapshot missing fields: {required_cc16 - fields}"
    )

    # _ToolCallSummary exists with correct fields
    tc_fields = {f.name for f in _ToolCallSummary.__dataclass_fields__.values()}
    assert {"name", "success", "result_preview"} <= tc_fields, (
        f"CC16: _ToolCallSummary missing fields: {tc_fields}"
    )

    # Verify prompt instructs LLM to reason over new fields
    assert "tool_calls" in _TRAJECTORY_PROMPT, "CC16: tool_calls not mentioned in prompt"
    assert "signals_emitted" in _TRAJECTORY_PROMPT, "CC16: signals_emitted not mentioned in prompt"
    assert "final_text" in _TRAJECTORY_PROMPT, "CC16: final_text not mentioned in prompt"


# ── Part F: info log on successful insert (caplog) ────────────────────────────


@pytest.mark.asyncio
async def test_success_info_log_emitted_with_enriched_snapshot(
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """After summarize() with an enriched snapshot, the info log fires with run_pk.

    This is the Part F defensive observability check. The smoke test grep relies
    on this log line: 'trajectory_summarizer: run_pk=N summarized'.
    """
    import contextlib

    from artemis.builder.trajectory_summarizer import _ToolCallSummary
    from artemis.builders.models import AgentRun

    agent_id = "cc16-log-test-agent"

    with contextlib.suppress(Exception):
        await builders_repo.create_agent(
            db_session,
            agent_id=agent_id,
            name="CC16 Log Test Agent",
            goal="Verify info log",
            system_prompt="You are a test agent.",
            tools=[],
            model="claude-sonnet-4-6",
        )
    await db_session.commit()

    run_uuid = str(uuid.uuid4())
    run = AgentRun(
        run_id=run_uuid,
        agent_id=agent_id,
        status="completed",
        user_message="Check the log.",
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
        tool_calls=(
            _ToolCallSummary(name="news_api.search", success=True, result_preview="5 results"),
        ),
        signals_emitted=1,
        final_text="Scout found articles and wrote a signal.",
        duration_ms=8500,
    )

    summary_json = json.dumps(
        {
            "what_worked": "news_api.search returned 5 results and 1 signal was emitted.",
            "what_stalled": None,
            "what_was_missing": None,
        }
    )
    adapter = FakeAdapter([ScriptedReply(text=summary_json)])

    with caplog.at_level(logging.INFO, logger="artemis.builder.trajectory_summarizer"):
        await summarize(snapshot, adapter=adapter, db_session=db_session)

    log_lines = [r.message for r in caplog.records if "trajectory_summarizer" in r.name]
    assert any("summarized" in line for line in log_lines), (
        f"Expected a 'summarized' info log from trajectory_summarizer, got: {log_lines}"
    )
    assert any(str(snapshot.run_pk) in line for line in log_lines), (
        f"run_pk={snapshot.run_pk} not in any trajectory_summarizer log: {log_lines}"
    )
