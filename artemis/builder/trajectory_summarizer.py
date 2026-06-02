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

from pydantic import ValidationError

from artemis.builder.trajectory_schemas import TrajectorySummary

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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _strip_markdown(text: str) -> str:
    """Strip leading/trailing markdown code fences from LLM output."""
    clean = text.strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        clean = "\n".join(lines[1:-1]) if len(lines) > 2 else clean
    return clean


def _extract_text(result_obj: Any) -> str:
    """Extract concatenated assistant text from a run_turn result."""
    from artemis.agent.types import TextBlock

    text = ""
    for msg in reversed(result_obj.messages):
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    text += block.text
            break
    return text


async def _summarize_with_retry(
    *,
    snapshot: AgentRunSnapshot,
    adapter: Any,
    initial_messages: list[Any],
    max_retries: int = 1,
) -> TrajectorySummary:
    """Call the LLM and validate output with Pydantic; retry once on failure.

    On persistent failure returns an all-null TrajectorySummary (safe default).
    Capped at 1 retry — no infinite loop possible.
    """
    from artemis.agent.loop import run_turn
    from artemis.agent.loop import user_message as _make_user_msg

    messages = list(initial_messages)
    for attempt in range(max_retries + 1):
        try:
            result_obj = await run_turn(
                adapter=adapter,
                messages=messages,
                max_tokens=512,
                max_iterations=1,
                cache_system=False,
                cache_tools=False,
            )
        except Exception:
            logger.exception(
                "trajectory_summarizer: LLM call failed for run_id=%s (attempt %d)",
                snapshot.run_id,
                attempt,
            )
            return TrajectorySummary()

        text = _extract_text(result_obj)
        clean = _strip_markdown(text)
        try:
            return TrajectorySummary.model_validate_json(clean)
        except ValidationError as exc:
            if attempt < max_retries:
                logger.warning(
                    "trajectory_summarizer: validation failed for run_id=%s (attempt %d); "
                    "retrying with error context: %s",
                    snapshot.run_id,
                    attempt,
                    exc,
                )
                # Append assistant reply + error feedback for the retry turn.
                messages = list(result_obj.messages) + [
                    _make_user_msg(
                        f"Your previous response failed Pydantic validation:\n{exc}\n\n"
                        "Please respond with valid JSON matching the required schema exactly. "
                        "No extra fields, no markdown fences."
                    )
                ]
            else:
                logger.warning(
                    "trajectory_summarizer: persistent validation failure for run_id=%s: %s",
                    snapshot.run_id,
                    exc,
                )
                return TrajectorySummary()

    # Should not be reachable but satisfies type checker.
    return TrajectorySummary()  # pragma: no cover


# ── M1: Memory observation write ─────────────────────────────────────────────


def _build_observation_content(
    run_id: str,
    generated_at_iso: str,
    what_worked: str | None,
    what_stalled: str | None,
    what_was_missing: str | None,
) -> str:
    """Compose a single-paragraph observation from the trajectory fields.

    Clauses for null/empty fields are omitted cleanly.
    """
    parts = [f"Run {run_id} ({generated_at_iso})."]
    if what_worked:
        parts.append(f"What worked: {what_worked}.")
    if what_stalled:
        parts.append(f"What stalled: {what_stalled}.")
    if what_was_missing:
        parts.append(f"What was missing: {what_was_missing}.")
    return " ".join(parts)


async def _write_trajectory_observation(
    *,
    run_id: str,
    run_pk: int,
    agent_id: str,
    what_worked: str | None,
    what_stalled: str | None,
    what_was_missing: str | None,
) -> None:
    """Write a memory observation from a trajectory summary, then link evidence.

    Opens a FRESH session from artemis.db.SessionLocal so the memory write is
    fully isolated from the trajectory session. This prevents deadlocks caused
    by session.begin_nested() (SAVEPOINT for embeddings) interacting with the
    outer trajectory session's transaction.

    Failure isolation: any exception is caught, logged as WARNING, and swallowed.
    The trajectory summary row (already committed) is the durable source-of-truth;
    the observation is an additive layer.
    """
    from datetime import UTC, datetime

    import artemis.db as _db
    from artemis.memory.schemas import Scope, SourceQualityHint
    from artemis.memory.store import get_or_create_scope, link_evidence, write_observation

    try:
        generated_at_iso = datetime.now(UTC).isoformat(timespec="seconds")
        content = _build_observation_content(
            run_id=run_id,
            generated_at_iso=generated_at_iso,
            what_worked=what_worked,
            what_stalled=what_stalled,
            what_was_missing=what_was_missing,
        )
        async with _db.SessionLocal() as mem_session:
            await get_or_create_scope(mem_session, scope_kind="agent", scope_id=agent_id)
            scope = Scope(scope_kind="agent", scope_id=agent_id)
            obs = await write_observation(
                mem_session,
                scope=scope,
                content=content,
                category="trajectory",
                source_quality=SourceQualityHint.agent,
            )
            await link_evidence(
                mem_session,
                observation_id=obs.id,
                source_kind="agent_run",
                source_id=str(run_pk),  # CC28: link_evidence now takes str
                weight=1.0,
            )
            await mem_session.commit()
        logger.info(
            "M1: observation id=%s written for agent_id=%s run_pk=%s",
            obs.id,
            agent_id,
            run_pk,
        )
    except Exception as exc:
        logger.warning(
            "M1 memory observation write failed for run_id=%s agent_id=%s: %s",
            run_id,
            agent_id,
            exc,
            exc_info=True,
        )


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
    from artemis.agent.loop import user_message
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

        # H3: use Pydantic-validated retry helper (1 retry max, then null fallback).
        parsed_summary = await _summarize_with_retry(
            snapshot=snapshot,
            adapter=adapter,
            initial_messages=[user_message(prompt)],
        )

        what_worked = parsed_summary.what_worked
        what_stalled = parsed_summary.what_stalled
        what_was_missing = parsed_summary.what_was_missing
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

        # M1 — write a memory observation from the trajectory summary.
        # Failure here MUST NOT break the trajectory write above.
        if snapshot.agent_id is not None:
            await _write_trajectory_observation(
                run_id=snapshot.run_id,
                run_pk=snapshot.run_pk,
                agent_id=snapshot.agent_id,
                what_worked=what_worked,
                what_stalled=what_stalled,
                what_was_missing=what_was_missing,
            )

    if db_session is not None:
        await _do_summarize(db_session)
    else:
        async with _db.SessionLocal() as session:
            await _do_summarize(session)
