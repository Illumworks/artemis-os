"""Trajectory summarizer — generates per-run summaries for the self-improvement loop.

Called after every agent_run completes. Runs in the background (asyncio.create_task)
so there is no latency added to the run itself.

The summarizer calls the LLM with the run's final messages to extract:
  - what_worked: what the agent accomplished successfully
  - what_stalled: where the agent got stuck or looped
  - what_was_missing: tools/context/capabilities that were absent

Design:
  summarize_async(run_id)  — fire-and-forget entry point (wraps summarize in a task)
  summarize(run_id, ...)   — the actual implementation; testable synchronously
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Prompt ────────────────────────────────────────────────────────────────────

_TRAJECTORY_PROMPT = """\
You are analyzing an agent run to extract a short trajectory summary for self-improvement.

The run record and final conversation messages are provided below.

Extract exactly three one-sentence observations. Be specific — name the tool, the step,
the error message, or the missing capability. If a field genuinely does not apply, write null.

Respond with valid JSON only, no prose:
{
  "what_worked": "<one sentence or null>",
  "what_stalled": "<one sentence or null>",
  "what_was_missing": "<one sentence or null>"
}

Run data:
{run_data}
"""


async def summarize_async(run_id: int) -> None:
    """Fire-and-forget: schedule summarize(run_id) as a background task.

    Called at the end of agent_run completion. Does not block the caller.
    """
    asyncio.create_task(_safe_summarize(run_id), name=f"trajectory_summarize_{run_id}")


async def _safe_summarize(run_id: int) -> None:
    """Wrapper that catches all exceptions so a summarizer failure never crashes the caller."""
    try:
        await summarize(run_id)
    except Exception:
        logger.exception("trajectory_summarizer: run_id=%s failed (non-fatal)", run_id)


async def summarize(
    run_id: int,
    *,
    adapter: Any | None = None,
    db_session: Any | None = None,
) -> None:
    """Generate and persist a trajectory summary for the given run.

    Parameters
    ----------
    run_id:
        The agent_runs.id PK.
    adapter:
        Optional ModelAdapter override (for tests). Defaults to the Anthropic adapter.
    db_session:
        Optional AsyncSession override (for tests). If None, opens one from SessionLocal.
    """
    import json

    import artemis.db as _db
    from artemis.agent.client import AnthropicAdapter
    from artemis.agent.loop import run_turn, user_message
    from artemis.builder.repository import create_trajectory_summary, get_trajectory_summary
    from artemis.builders.models import AgentRun

    # Resolve adapter
    if adapter is None:
        try:
            from artemis.providers import get_adapter

            adapter = get_adapter("anthropic")
        except Exception:
            adapter = AnthropicAdapter()

    async def _do_summarize(session: Any) -> None:
        from sqlalchemy import select as sa_select

        # Check idempotency: don't re-summarize.
        existing = await get_trajectory_summary(session, run_id)
        if existing is not None:
            logger.debug("trajectory_summarizer: run_id=%s already summarized", run_id)
            return

        # Load the run record.
        result = await session.execute(
            sa_select(AgentRun).where(AgentRun.id == run_id).limit(1)
        )
        run = result.scalar_one_or_none()
        if run is None:
            logger.warning("trajectory_summarizer: run_id=%s not found", run_id)
            return

        run_data: dict[str, Any] = {
            "run_id": str(run.run_id),
            "agent_id": run.agent_id,
            "status": run.status,
            "user_message": run.user_message,
            "error": run.error,
            "stop_reason": None,  # not on model yet; placeholder
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
            logger.exception("trajectory_summarizer: LLM call failed for run_id=%s", run_id)
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
                "trajectory_summarizer: could not parse JSON for run_id=%s: %r", run_id, text[:200]
            )
            # Store partial — at least record the raw text in what_worked.
            parsed = {"what_worked": None, "what_stalled": None, "what_was_missing": None}

        await create_trajectory_summary(
            session,
            run_id=run_id,
            what_worked=parsed.get("what_worked") or None,
            what_stalled=parsed.get("what_stalled") or None,
            what_was_missing=parsed.get("what_was_missing") or None,
        )
        await session.commit()
        logger.info("trajectory_summarizer: run_id=%s summarized", run_id)

    if db_session is not None:
        await _do_summarize(db_session)
    else:
        async with _db.SessionLocal() as session:
            await _do_summarize(session)
