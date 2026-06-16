"""P6 self-evolution — execution trace capture.

Public API
----------
capture_trace(...)       — fire-and-forget: schedule trace insertion as a
                           background asyncio task. Never raises into the caller.
record_trace(...)        — synchronous inner write used directly by tests (and
                           indirectly by capture_trace via the background task).
get_recent_traces(...)   — query helper for the P6 consumer: returns the N most
                           recent traces for a given agent_id.

Relationship to trajectory_summarizer
--------------------------------------
``agent_run_trajectory_summaries`` (written by the trajectory_summarizer) are:
  - LLM-generated — require an extra model call after each run.
  - Per-*agent_run* — only exist for runs that go through the executor path.
  - Narrative text: what_worked / what_stalled / what_was_missing.
  - Available ~seconds after the run (async background task).

``agent_traces`` (written here) are:
  - Zero-LLM — structured fields captured from live runtime state.
  - Per-*turn* — covers floating-agent DM/Slack turns that have no agent_run row.
  - Numeric/structured: latency_ms, tokens, tools_used[], outcome, error.
  - Written at end-of-turn, same async-task pattern as the cost recorder.

The P6 consumer needs both layers: traces for raw performance signals,
trajectory summaries for qualitative narrative context.

Non-blocking invariant
-----------------------
``capture_trace`` wraps the DB write in ``asyncio.create_task`` and swallows
all exceptions so that a trace-DB failure NEVER bubbles into the caller's turn.
The background-task reference is held in ``_BACKGROUND_TASKS`` (same GC-guard
pattern as the trajectory_summarizer) so the task is not GC'd before it runs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# ── GC-retention guard (mirrors trajectory_summarizer CC7 pattern) ────────────
# asyncio.create_task() returns a weakly-referenced Task. Holding a strong
# reference here prevents GC before execution; the done-callback drops it.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


# ── Public: fire-and-forget capture ──────────────────────────────────────────


def capture_trace(
    *,
    agent_id: str,
    feature_tag: str,
    session_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_summary: str | None = None,
    tools_used: list[str] | None = None,
    output_summary: str | None = None,
    outcome: str = "success",
    error: str | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    owner_user_id: int | None = None,
) -> None:
    """Schedule a trace insert as a fire-and-forget background task.

    This is the call-site API.  It is synchronous and returns immediately;
    the actual DB write happens in a background asyncio task.

    Design: never raises, never blocks.  A failure in the background task
    is logged at WARNING level and discarded.

    Parameters
    ----------
    agent_id:
        Identifier of the agent making the turn (e.g. "artemis", "callie").
    feature_tag:
        Logical feature context, e.g. "floating_artemis", "agent_run".
        Mirrors the feature_tag used in cost_events for easy correlation.
    session_id:
        The floating_artemis_sessions.session_id for web/Slack turns, or
        the AgentRun.run_id for executor turns.  None for cron/background.
    provider:
        LLM provider string, e.g. "anthropic", "claude-code", "gemini".
    model:
        Model slug, e.g. "claude-sonnet-4-6".
    input_summary:
        First ≤500 chars of the user's message (or a digest).  Keep short;
        this is for pattern analysis, not replay.
    tools_used:
        Ordered list of tool names called during the turn.
    output_summary:
        First ≤500 chars of the final assistant response.
    outcome:
        One of "success" | "error" | "partial" | "tool_pending".
    error:
        Error string when outcome=="error", else None.
    latency_ms:
        Wall-clock turn duration in milliseconds.
    input_tokens, output_tokens:
        Token counts from the provider Usage object.  None when unavailable.
    owner_user_id:
        Owner user DB PK for row-level ownership.  None for system turns.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop — this shouldn't happen in production (all
        # callers are async), but swallow gracefully.
        logger.debug("capture_trace: no running event loop; skipping trace for agent=%s", agent_id)
        return

    task = loop.create_task(
        _safe_record_trace(
            agent_id=agent_id,
            feature_tag=feature_tag,
            session_id=session_id,
            provider=provider,
            model=model,
            input_summary=input_summary,
            tools_used=tools_used or [],
            output_summary=output_summary,
            outcome=outcome,
            error=error,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            owner_user_id=owner_user_id,
        ),
        name=f"trace_capture_{agent_id}_{feature_tag}",
    )
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


# ── Internal async wrapper (swallows all exceptions) ─────────────────────────


async def _safe_record_trace(**kwargs: Any) -> None:
    try:
        await _do_record_trace(**kwargs)
    except Exception:
        logger.warning(
            "capture_trace: write failed for agent=%s feature=%s (non-fatal)",
            kwargs.get("agent_id"),
            kwargs.get("feature_tag"),
            exc_info=True,
        )


async def _do_record_trace(
    *,
    agent_id: str,
    feature_tag: str,
    session_id: str | None,
    provider: str | None,
    model: str | None,
    input_summary: str | None,
    tools_used: list[str],
    output_summary: str | None,
    outcome: str,
    error: str | None,
    latency_ms: int | None,
    input_tokens: int | None,
    output_tokens: int | None,
    owner_user_id: int | None,
) -> None:
    import artemis.db as _db
    from artemis.builders.models import AgentTrace

    async with _db.SessionLocal() as session:
        row = AgentTrace(
            agent_id=agent_id,
            session_id=session_id,
            feature_tag=feature_tag,
            provider=provider,
            model=model,
            input_summary=input_summary[:500] if input_summary else None,
            tools_used=tools_used,
            output_summary=output_summary[:500] if output_summary else None,
            outcome=outcome,
            error=error[:2000] if error else None,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            owner_user_id=owner_user_id,
        )
        session.add(row)
        await session.commit()
    logger.debug(
        "capture_trace: wrote trace for agent=%s feature=%s outcome=%s latency_ms=%s",
        agent_id,
        feature_tag,
        outcome,
        latency_ms,
    )


# ── Synchronous helper for tests (bypasses asyncio task, uses injected session) ──


async def record_trace(
    session: Any,
    *,
    agent_id: str,
    feature_tag: str,
    session_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    input_summary: str | None = None,
    tools_used: list[str] | None = None,
    output_summary: str | None = None,
    outcome: str = "success",
    error: str | None = None,
    latency_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    owner_user_id: int | None = None,
) -> Any:
    """Insert a trace row using the provided session and return the row.

    Used by tests and by callers that already have a session in scope.
    Does NOT commit — the caller owns the transaction.
    """
    from artemis.builders.models import AgentTrace

    row = AgentTrace(
        agent_id=agent_id,
        session_id=session_id,
        feature_tag=feature_tag,
        provider=provider,
        model=model,
        input_summary=input_summary[:500] if input_summary else None,
        tools_used=tools_used or [],
        output_summary=output_summary[:500] if output_summary else None,
        outcome=outcome,
        error=error[:2000] if error else None,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        owner_user_id=owner_user_id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


# ── Read helper for P6 consumer ───────────────────────────────────────────────


async def get_recent_traces(
    session: Any,
    agent_id: str,
    *,
    limit: int = 50,
    feature_tag: str | None = None,
) -> list[Any]:
    """Return the most recent traces for ``agent_id``, newest first.

    Parameters
    ----------
    session:
        SQLAlchemy AsyncSession.
    agent_id:
        Filter to this agent.
    limit:
        Maximum rows to return (default 50; P6 consumer should set this
        to whatever its analysis window needs).
    feature_tag:
        Optional additional filter (e.g. "floating_artemis" only).

    Returns
    -------
    list[AgentTrace]
        Ordered newest-first.  Empty list on any error.
    """
    try:
        from sqlalchemy import select

        from artemis.builders.models import AgentTrace

        stmt = (
            select(AgentTrace)
            .where(AgentTrace.agent_id == agent_id)
            .order_by(AgentTrace.created_at.desc())
            .limit(limit)
        )
        if feature_tag is not None:
            stmt = stmt.where(AgentTrace.feature_tag == feature_tag)

        result = await session.execute(stmt)
        return list(result.scalars().all())
    except Exception:
        logger.warning(
            "get_recent_traces: query failed for agent=%s (returning [])",
            agent_id,
            exc_info=True,
        )
        return []


# ── Timing helper ─────────────────────────────────────────────────────────────


def start_timer() -> float:
    """Return a monotonic start timestamp for latency measurement."""
    return time.monotonic()


def elapsed_ms(start: float) -> int:
    """Return milliseconds elapsed since ``start_timer()``."""
    return int((time.monotonic() - start) * 1000)
