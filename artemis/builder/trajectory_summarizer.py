"""Trajectory summarizer — generates per-run summaries for the self-improvement loop.

Called after every agent_run completes. Runs in the background (asyncio.create_task)
so there is no latency added to the run itself.

The summarizer calls the LLM with the run's final messages to extract:
  - what_worked: what the agent accomplished successfully
  - what_stalled: where the agent got stuck or looped
  - what_was_missing: tools/context/capabilities that were absent

Design:
  summarize_async(snapshot)  — fire-and-forget entry point (wraps summarize in a task)
  summarize(snapshot, ...)   — the actual implementation; testable synchronously

CC13: The summarizer no longer queries the DB for the AgentRun row.  The caller
builds an AgentRunSnapshot from the in-scope run object (before commit) and passes
it directly.  This eliminates the transaction-visibility race where the async task's
new session opened after the flush (but before the commit) could not see the row.

CC16: AgentRunSnapshot now carries structured tool-call extracts, signal emission
counts, the agent's final text, and wall-clock duration — so the LLM has real
data to reason over instead of meta-complaining about missing transcripts.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ToolCallSummary:
    """Structured summary of a single tool call, extracted from a ToolUseBlock pair.

    Attributes
    ----------
    name:
        Tool name (e.g. "signal_queue.write", "news_api.search").
    success:
        False when the ToolResultBlock carried is_error=True.
    result_preview:
        First ~100 chars of the result content (or error string).
    """

    name: str
    success: bool
    result_preview: str  # ~100 chars, no raw transcript


@dataclass(frozen=True)
class AgentRunSnapshot:
    """Immutable snapshot of the fields needed by the trajectory summarizer.

    Constructed by the caller (executor) from the in-scope AgentRun object
    immediately after flush, then passed to summarize_async(). This avoids
    a DB re-query in a separate session that cannot see the unflushed row.

    Attributes
    ----------
    run_id:
        The UUID string (AgentRun.run_id). Used in log messages and as the
        human-readable identifier in the prompt.
    run_pk:
        The integer primary key (AgentRun.id). Used as the FK when inserting
        the agent_run_trajectory_summaries row.
    agent_id:
        The agent identifier string, or None for anonymous runs.
    status:
        Terminal status of the run ("completed", "failed", etc.).
    user_message:
        The original user message that triggered the run, or None.
    error:
        Error string if the run failed, or None.
    tool_calls:
        Ordered sequence of tool calls the agent made, with success/failure
        and a short result preview. Empty tuple for no-tool runs.
    signals_emitted:
        Count of signal_queue rows written by this run
        (provenance->>'agent_run_id' == run_id). Set by executor post-commit.
    final_text:
        Last assistant text message, truncated to ~500 chars.
    duration_ms:
        Wall-clock duration in milliseconds (completed_at - started_at).
        None if timestamps unavailable.
    """

    run_id: str
    run_pk: int
    agent_id: str | None
    status: str
    user_message: str | None
    error: str | None
    # CC16 enrichment fields
    tool_calls: tuple[_ToolCallSummary, ...] = ()
    signals_emitted: int = 0
    final_text: str | None = None
    duration_ms: int | None = None


# ── Background-task retention (CC7 pattern) ───────────────────────────────────
# A bare asyncio.create_task() return value is weakly referenced by the event
# loop — the GC can collect it before it runs.  Holding a strong reference in
# this module-level set prevents GC; the done-callback discards the reference
# once the task finishes so the set does not grow without bound.

_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()

# ── Prompt ────────────────────────────────────────────────────────────────────

_TRAJECTORY_PROMPT = """\
You are analyzing an agent run to extract a short trajectory summary for self-improvement.

The run record is provided below. It includes:
- Basic run metadata (status, user_message, error)
- tool_calls: the ordered sequence of tools the agent called, each with success/failure
  and a result preview (~100 chars)
- signals_emitted: how many signal_queue rows this run wrote
- final_text: the agent's last assistant message (~500 chars)
- duration_ms: wall-clock time for the run

Extract exactly three one-sentence observations. Be specific — name the tool, the step,
the error message, the signal count, or the missing capability.

Look at the tool_calls sequence: which tools did the agent call, did they succeed, did
the agent never call expected tools (e.g. did a scout never call signal_queue.write)?
Look at signals_emitted: did the agent produce work product (signals_emitted > 0)?
Look at final_text: did the agent state confusion, ask for clarification, or describe
what it did?

If a field genuinely does not apply, write null.

Respond with valid JSON only, no prose:
{{
  "what_worked": "<one sentence or null>",
  "what_stalled": "<one sentence or null>",
  "what_was_missing": "<one sentence or null>"
}}

Run data:
{run_data}
"""


async def summarize_async(snapshot: AgentRunSnapshot) -> None:
    """Fire-and-forget: schedule summarize(snapshot) as a background task.

    Called at the end of agent_run completion. Does not block the caller.
    The snapshot carries all data the summarizer needs — no DB lookup occurs.
    """
    task = asyncio.create_task(
        _safe_summarize(snapshot), name=f"trajectory_summarize_{snapshot.run_id}"
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def _safe_summarize(snapshot: AgentRunSnapshot) -> None:
    """Wrapper that catches all exceptions so a summarizer failure never crashes the caller."""
    try:
        await summarize(snapshot)
    except Exception:
        logger.exception("trajectory_summarizer: unhandled exception during summarize")


async def summarize(
    snapshot: AgentRunSnapshot,
    *,
    adapter: Any | None = None,
    db_session: Any | None = None,
) -> None:
    """Generate and persist a trajectory summary from the provided snapshot.

    Parameters
    ----------
    snapshot:
        Pre-built AgentRunSnapshot with all fields needed. No DB lookup is performed.
    adapter:
        Optional ModelAdapter override (for tests). Defaults to provider cascade.
    db_session:
        Optional AsyncSession override (for tests). If None, opens one from SessionLocal.
    """
    import json

    import artemis.db as _db
    from artemis.agent.client import AnthropicAdapter
    from artemis.agent.loop import run_turn, user_message
    from artemis.builder.repository import create_trajectory_summary, get_trajectory_summary

    # Resolve adapter via provider cascade (claude-code → codex → lm-studio → anthropic)
    if adapter is None:
        from artemis.providers import get_adapter
        from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

        for _candidate in ("claude-code", "codex", "lm-studio", "anthropic"):
            try:
                adapter = get_adapter(_candidate)
                break
            except (MissingApiKeyError, UnknownProviderError):
                continue
            except Exception:
                continue
        if adapter is None:
            adapter = AnthropicAdapter()

    async def _do_summarize(session: Any) -> None:
        # Check idempotency: don't re-summarize.
        existing = await get_trajectory_summary(session, snapshot.run_pk)
        if existing is not None:
            logger.debug("trajectory_summarizer: run_id=%s already summarized", snapshot.run_id)
            return

        # Build prompt directly from snapshot — no DB lookup needed.
        run_data: dict[str, Any] = {
            "run_id": snapshot.run_id,
            "agent_id": snapshot.agent_id,
            "status": snapshot.status,
            "user_message": snapshot.user_message,
            "error": snapshot.error,
            "stop_reason": None,  # not on model yet; placeholder
            # CC16: structured enrichment fields
            "tool_calls": [
                {
                    "name": tc.name,
                    "success": tc.success,
                    "result_preview": tc.result_preview,
                }
                for tc in snapshot.tool_calls
            ],
            "signals_emitted": snapshot.signals_emitted,
            "final_text": snapshot.final_text,
            "duration_ms": snapshot.duration_ms,
        }

        prompt = _TRAJECTORY_PROMPT.format(run_data=json.dumps(run_data, indent=2))

        try:
            result_obj = await run_turn(
                adapter=adapter,
                messages=[user_message(prompt)],
                max_tokens=512,
                max_iterations=1,
                cache_system=False,
                cache_tools=False,
            )
        except Exception:
            logger.exception(
                "trajectory_summarizer: LLM call failed for run_id=%s", snapshot.run_id
            )
            return

        # Extract the assistant's text.
        from artemis.agent.types import TextBlock

        text = ""
        for msg in reversed(result_obj.messages):
            if msg.role == "assistant":
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text += block.text
                break

        # Parse JSON response.
        parsed: dict[str, Any] = {}
        try:
            # Strip markdown fences if present.
            clean = text.strip()
            if clean.startswith("```"):
                lines = clean.splitlines()
                clean = "\n".join(lines[1:-1]) if len(lines) > 2 else clean
            parsed = json.loads(clean)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "trajectory_summarizer: could not parse JSON for run_id=%s: %r",
                snapshot.run_id,
                text[:200],
            )
            # Store partial — all-null fields preserve the row for audit.
            parsed = {"what_worked": None, "what_stalled": None, "what_was_missing": None}

        what_worked = parsed.get("what_worked") or None
        what_stalled = parsed.get("what_stalled") or None
        what_was_missing = parsed.get("what_was_missing") or None
        await create_trajectory_summary(
            session,
            run_id=snapshot.run_pk,
            what_worked=what_worked,
            what_stalled=what_stalled,
            what_was_missing=what_was_missing,
        )
        await session.commit()
        logger.info(
            "trajectory_summarizer: run_pk=%s summarized (worked=%s..., stalled=%s..., missing=%s...)",
            snapshot.run_pk,
            (what_worked or "")[:60],
            (what_stalled or "")[:60],
            (what_was_missing or "")[:60],
        )

    if db_session is not None:
        await _do_summarize(db_session)
    else:
        async with _db.SessionLocal() as session:
            await _do_summarize(session)
