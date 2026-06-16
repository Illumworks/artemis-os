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
from artemis.costs.events import record_cost_event

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

    GeminiRateLimitError is re-raised so the caller (_do_summarize) can fall
    back to claude-code.  All other exceptions are caught and return the safe
    null default.
    """
    from artemis.agent.loop import run_turn
    from artemis.agent.loop import user_message as _make_user_msg
    from artemis.providers.errors import GeminiRateLimitError

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
        except GeminiRateLimitError:
            # Re-raise so the caller can fall through to claude-code.
            raise
        except Exception:
            logger.exception(
                "trajectory_summarizer: LLM call failed for run_id=%s (attempt %d)",
                snapshot.run_id,
                attempt,
            )
            return TrajectorySummary()

        # Record cost — failure must never propagate.
        try:
            from artemis.costs.events import adapter_identity
            from artemis.db import SessionLocal

            _provider, _model, _path = adapter_identity(adapter)
            async with SessionLocal() as _cost_session:
                await record_cost_event(
                    _cost_session,
                    provider=_provider,
                    model=_model,
                    provider_path=_path,
                    feature_tag="trajectory_summary",
                    input_tokens=getattr(result_obj.usage, "input_tokens", 0)
                    if result_obj.usage
                    else 0,
                    output_tokens=getattr(result_obj.usage, "output_tokens", 0)
                    if result_obj.usage
                    else 0,
                )
                await _cost_session.commit()
        except Exception:
            logger.warning(
                "cost_event recording failed in trajectory_summarizer run_id=%s",
                snapshot.run_id,
                exc_info=True,
            )

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

# ── P5 auto-trigger constants ─────────────────────────────────────────────────
# After this many successful runs (with trajectory summaries) since the last
# skill distillation, automatically fire the distiller for the agent.
_DISTILL_AFTER_N_RUNS: int = 5

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


async def _count_runs_since_last_distill(session: Any, agent_id: str) -> int:
    """Count successful runs with trajectory summaries created after the most
    recent self-improvement skill proposal for this agent.

    If no prior distillation exists for the agent, counts all successful
    summarized runs.  Returns 0 on any DB error (fail-safe).

    A "successful run" for counting purposes is one where status == 'completed'.
    We only count runs that already have a trajectory summary, because those are
    the ones the distiller would examine.
    """
    try:
        from sqlalchemy import text as sa_text

        # Find the created_at of the most recent self-improvement skill proposal
        # that cites this agent (via citations->>'agent_id').
        # If none exist, we count from the beginning of time.
        last_distill_q = sa_text(
            """
            SELECT MAX(created_at) AS last_distill_at
            FROM definition_proposals
            WHERE kind = 'skill'
              AND proposed_by = 'self-improvement'
              AND citations->>'agent_id' = :agent_id
              AND status IN ('pending', 'approved')
            """
        )
        result = await session.execute(last_distill_q, {"agent_id": agent_id})
        row = result.fetchone()
        last_distill_at = row.last_distill_at if row and row.last_distill_at else None

        # Count completed runs with trajectory summaries since last_distill_at.
        # Use CAST to avoid asyncpg type-ambiguity for the nullable timestamp param.
        count_q = sa_text(
            """
            SELECT COUNT(DISTINCT ar.id) AS run_count
            FROM agent_runs ar
            JOIN agent_run_trajectory_summaries ats ON ats.run_id = ar.id
            WHERE ar.agent_id = :agent_id
              AND ar.status = 'completed'
              AND (
                CAST(:last_distill_at AS timestamptz) IS NULL
                OR ats.generated_at > CAST(:last_distill_at AS timestamptz)
              )
            """
        )
        count_result = await session.execute(
            count_q,
            {"agent_id": agent_id, "last_distill_at": last_distill_at},
        )
        count_row = count_result.fetchone()
        return int(count_row.run_count) if count_row else 0
    except Exception:
        logger.warning(
            "trajectory_summarizer: _count_runs_since_last_distill failed for agent=%r",
            agent_id,
            exc_info=True,
        )
        return 0


async def _safe_auto_distill(agent_id: str) -> None:
    """Fire-and-forget skill distillation for agent_id.

    Fail-safe: any exception is caught and logged; the summarizer is not affected.
    Human-gated: only creates proposals, never auto-approves.
    """
    try:
        import artemis.db as _db
        from artemis.builder.skill_distiller import distill_skill_candidates

        async with _db.SessionLocal() as session:
            result = await distill_skill_candidates(session, agent_id)
        logger.info(
            "trajectory_summarizer: auto-distill for agent=%r → proposed=%d skipped=%d",
            agent_id,
            result.get("n_proposed", 0),
            result.get("n_skipped", 0),
        )
    except Exception:
        logger.warning(
            "trajectory_summarizer: auto-distill failed for agent=%r (non-fatal)",
            agent_id,
            exc_info=True,
        )


async def _safe_maybe_auto_distill(agent_id: str, run_id: str) -> None:
    """Check run count and conditionally fire the distiller.

    Opens its own DB session (isolated from the summarizer session) so that
    it never touches the session that was used to write the trajectory summary.
    This keeps the cc13 spy-session test clean.

    Fail-safe: any exception is caught and logged.
    """
    try:
        import artemis.db as _db

        async with _db.SessionLocal() as session:
            run_count = await _count_runs_since_last_distill(session, agent_id)

        logger.debug(
            "trajectory_summarizer: agent=%r has %d successful run(s) since last distill (threshold=%d)",
            agent_id,
            run_count,
            _DISTILL_AFTER_N_RUNS,
        )
        if run_count >= _DISTILL_AFTER_N_RUNS and run_count % _DISTILL_AFTER_N_RUNS == 0:
            # Only fire at exact multiples: 5, 10, 15, …
            logger.info(
                "trajectory_summarizer: auto-trigger distiller for agent=%r (run_count=%d)",
                agent_id,
                run_count,
            )
            await _safe_auto_distill(agent_id)
    except Exception:
        logger.warning(
            "trajectory_summarizer: auto-trigger check failed for agent=%r run=%r (non-fatal)",
            agent_id,
            run_id,
            exc_info=True,
        )


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

    # Resolve adapter via override-aware async resolver.
    # When routing assigns Gemini as primary, GeminiRateLimitError from run_turn
    # is caught below and the call is retried on claude-code.
    if adapter is None:
        from artemis.providers import get_adapter
        from artemis.providers.errors import (
            MissingApiKeyError,
            MissingCliBinaryError,
            UnknownProviderError,
        )

        # Determine primary provider from DB routing override.
        _primary_provider = "claude-code"
        try:
            async with _db.SessionLocal() as _override_session:
                from artemis.providers.routing_repository import get_routing_override_for_feature

                override = await get_routing_override_for_feature(
                    _override_session, "trajectory_summary"
                )
                if override and override.cascade:
                    _primary_provider = override.cascade[0].get("provider", "claude-code")
        except Exception:
            pass

        # For Gemini, request the flash model explicitly (reasoning task needs
        # more capability than flash-lite).
        _adapter_kwargs: dict[str, str] = (
            {"default_model": "gemini-2.5-flash"} if _primary_provider == "gemini" else {}
        )
        try:
            adapter = get_adapter(_primary_provider, **_adapter_kwargs)
        except (MissingApiKeyError, MissingCliBinaryError, UnknownProviderError):
            logger.warning(
                "trajectory_summarizer: primary provider %r unavailable; falling back to claude-code",
                _primary_provider,
            )
            _primary_provider = "claude-code"
            try:
                adapter = get_adapter("claude-code")
            except Exception:
                logger.warning(
                    "trajectory_summarizer: adapter resolution failed, falling back to AnthropicAdapter"
                )
                adapter = AnthropicAdapter()
                _primary_provider = "anthropic"
        except Exception:
            logger.warning(
                "trajectory_summarizer: adapter resolution failed, falling back to AnthropicAdapter"
            )
            adapter = AnthropicAdapter()
            _primary_provider = "anthropic"

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
        # If the primary provider raises GeminiRateLimitError, fall through to
        # claude-code and retry the full summarization.
        from artemis.providers.errors import GeminiRateLimitError

        try:
            parsed_summary = await _summarize_with_retry(
                snapshot=snapshot,
                adapter=adapter,
                initial_messages=[user_message(prompt)],
            )
        except GeminiRateLimitError:
            logger.warning(
                "trajectory_summarizer: Gemini rate-limited for run_id=%s; "
                "falling back to claude-code",
                snapshot.run_id,
            )
            try:
                from artemis.providers import get_adapter

                _fallback_adapter = get_adapter("claude-code")
            except Exception:
                logger.warning(
                    "trajectory_summarizer: claude-code fallback unavailable for run_id=%s",
                    snapshot.run_id,
                )
                return
            parsed_summary = await _summarize_with_retry(
                snapshot=snapshot,
                adapter=_fallback_adapter,
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

    # P5 — auto-trigger: after N=5 successful summarized runs since last
    # distillation, fire the distiller fire-and-forget (own session, isolated
    # from the summarizer session so it never affects the spy in tests or the
    # main session's transaction visibility).
    # Only fires for completed runs; fail-safe (never crashes the summarizer).
    if snapshot.agent_id is not None and snapshot.status == "completed":
        task = asyncio.create_task(
            _safe_maybe_auto_distill(snapshot.agent_id, snapshot.run_id),
            name=f"auto_distill_check_{snapshot.agent_id}_{snapshot.run_id}",
        )
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
