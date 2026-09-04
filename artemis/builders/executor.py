"""Agent executor — wires F2a agent definitions to the F1 agent loop.

Public function: run_agent()

This module is the "execution" half of the Builders domain. It loads an Agent
row, creates an AgentRun record, drives run_turn, writes results to
agent_context, and returns the updated AgentRun.
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

import artemis.tools  # noqa: F401 — registers tool factories at import time
from artemis.agent import AnthropicAdapter, collect_tools_used, run_turn
from artemis.agent.client import ModelAdapter
from artemis.agent.hooks import HookRegistry
from artemis.agent.tools import ToolRegistry
from artemis.builders.models import AgentRun
from artemis.builders.repository import (
    create_agent_run,
    get_agent,
    list_skills_for_agent,
    set_agent_context,
    set_agent_run_completed,
)
from artemis.costs.events import record_cost_event
from artemis.marketing.josh_spec import JoshSpec, parse_spec, reason_codes_for_scout
from artemis.tools.context import ToolContext
from artemis.tools.registry import get_factory, known_tool_names
from artemis.trace.capture import elapsed_ms, record_trace, start_timer
from artemis.ws.events import (
    agent_completed_event,
    agent_failed_event,
    agent_message_event,
    agent_started_event,
    tool_completed_event,
    tool_started_event,
)
from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _cached_josh_spec() -> JoshSpec:
    return parse_spec()


def _build_system_prompt(
    agent: Any,
    shared_context: dict[str, Any] | None,
) -> str | None:
    """Compose the rich system prompt from agent row + Josh's spec.

    Returns None only if every section would be empty (legacy compatibility for
    agents with no system_prompt + no goal + no persona).
    """
    parts: list[str] = []

    # Base system prompt
    if agent.system_prompt:
        parts.append(agent.system_prompt)

    # Persona sections (populated by F3; gracefully absent pre-F3)
    persona = agent.persona or {}
    if isinstance(persona, dict):
        voice_notes = persona.get("voice_notes") or ""
        purpose = persona.get("purpose") or ""
        if voice_notes:
            parts.append(f"## Voice\n{voice_notes}")
        if purpose:
            parts.append(f"## Purpose\n{purpose}")

    # Goal
    if agent.goal:
        parts.append(f"## Goal\n{agent.goal}")

    # Scout-only: Josh-spec reason codes + state nuances
    agent_id: str = agent.agent_id or ""
    if agent_id.startswith("marketing.scout."):
        scout_slug = agent_id.rsplit(".", 1)[-1]
        spec = _cached_josh_spec()
        scout_codes = reason_codes_for_scout(spec, scout_slug)
        if scout_codes:
            lines = [
                "## Reason codes you may emit\n\n"
                "You may emit ONLY these reason codes. Any other code will be rejected.\n"
            ]
            for rc in scout_codes:
                lines.append(
                    f"- **{rc.code}** ({rc.default_urgency}) — {rc.description}\n"
                    f"  Scout's job: {rc.what_scout_looks_for}"
                )
            parts.append("\n".join(lines))

        if spec.state_nuances:
            nuance_lines = ["## State nuances to watch"]
            for sn in spec.state_nuances:
                nuance_lines.append(f"\n### {sn.state}\n{sn.text}")
            parts.append("\n".join(nuance_lines))

    # Urgency tiers (populated by F3; gracefully absent pre-F3)
    urgency_tiers = agent.urgency_tiers
    if urgency_tiers and isinstance(urgency_tiers, dict):
        tier_lines = ["## Urgency discipline"]
        for tier, desc in urgency_tiers.items():
            tier_lines.append(f"- **{tier}**: {desc}")
        parts.append("\n".join(tier_lines))

    # Failure modes (populated by F3; gracefully absent pre-F3)
    failure_modes = agent.failure_modes
    if failure_modes and isinstance(failure_modes, list):
        fm_lines = ["## Failure modes to avoid"]
        for fm in failure_modes:
            name = fm.get("name", "")
            desc = fm.get("description", "")
            fm_lines.append(f"- **{name}** — {desc}")
        parts.append("\n".join(fm_lines))

    # Implementation notes (populated by F3; gracefully absent pre-F3)
    if agent.implementation_notes:
        parts.append(f"## Implementation notes\n{agent.implementation_notes}")

    # Inputs required (populated by F3; gracefully absent pre-F3)
    inputs_required = agent.inputs_required
    if inputs_required and isinstance(inputs_required, list):
        inp_lines = ["## Inputs available"]
        for inp in inputs_required:
            inp_lines.append(f"- {inp}")
        parts.append("\n".join(inp_lines))

    # Shared context from upstream pipeline nodes
    if shared_context:
        ctx_lines = "\n".join(f"{k}: {v}" for k, v in shared_context.items())
        parts.append(f"## Context\n{ctx_lines}")

    return "\n\n".join(parts) if parts else None


def _build_agent_hooks(run_id: str) -> HookRegistry:
    """Build a HookRegistry that broadcasts WS events for a given run_id."""
    hooks = HookRegistry()

    async def on_message(message: object) -> None:
        from artemis.agent.types import Message

        if isinstance(message, Message):
            content = []
            for block in message.content:
                if hasattr(block, "text"):
                    content.append({"type": "text", "text": block.text})
                elif hasattr(block, "name"):
                    # ToolUseBlock
                    content.append(
                        {
                            "type": "tool_use",
                            "id": getattr(block, "id", ""),
                            "name": block.name,
                            "input": getattr(block, "input", {}),
                        }
                    )
                elif hasattr(block, "tool_use_id"):
                    # ToolResultBlock
                    content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": getattr(block, "content", ""),
                            "is_error": getattr(block, "is_error", False),
                        }
                    )
            event = agent_message_event(run_id, message.role, content)
            await ws_manager.broadcast(run_id, event.to_dict())

    async def before_tool(payload: object) -> None:
        if not isinstance(payload, dict):
            return
        event = tool_started_event(
            run_id,
            name=payload.get("name", ""),
            input=payload.get("input", {}),
            tool_use_id=payload.get("tool_use_id", ""),
        )
        await ws_manager.broadcast(run_id, event.to_dict())

    async def after_tool(payload: object) -> None:
        if not isinstance(payload, dict):
            return
        event = tool_completed_event(
            run_id,
            name=payload.get("name", ""),
            input=payload.get("input", {}),
            tool_use_id=payload.get("tool_use_id", ""),
            result=str(payload.get("result", "")),
            is_error=bool(payload.get("is_error", False)),
            elapsed_ms=int(payload.get("elapsed_ms", 0)),
        )
        await ws_manager.broadcast(run_id, event.to_dict())

    async def on_done(result: object) -> None:
        from artemis.agent.types import RunResult

        if isinstance(result, RunResult):
            event = agent_completed_event(
                run_id,
                stop_reason=result.stop_reason,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
            await ws_manager.broadcast(run_id, event.to_dict())

    hooks.on("on_message", on_message)
    hooks.on("before_tool", before_tool)
    hooks.on("after_tool", after_tool)
    hooks.on("on_done", on_done)
    return hooks


# P5 learning loop — skill injection constants (Decision #3)
_SKILL_MAX_COUNT = 3
_SKILL_MAX_TOKENS_APPROX = 200  # ~200 tokens ≈ 800 characters
_SKILL_MAX_CHARS = _SKILL_MAX_TOKENS_APPROX * 4  # conservative char estimate


async def _inject_skills_into_prompt(
    session: AsyncSession,
    agent: Any,
    system_prompt: str | None,
) -> tuple[str | None, list[Any]]:
    """Load approved skills assigned to the agent and append a 'Learned skills'
    block to the system prompt.

    Decision #3 guardrails:
    - Only approved skills with non-empty instructions
    - Only skills whose tools[] overlap the agent's tool list
    - Hard cap: max 3 skills, ~200 tokens of instructions each

    Returns (updated_system_prompt, injected_skills) where injected_skills is
    the list of Skill rows actually appended (used for usage tracking).

    Fail-safe: any error → return original prompt + empty list (no crash).
    """
    from datetime import UTC, datetime

    try:
        all_skills = await list_skills_for_agent(session, agent.id)
    except Exception:
        logger.warning(
            "skill_injection: failed to load skills for agent=%r — skipping",
            agent.agent_id,
            exc_info=True,
        )
        return system_prompt, []

    if not all_skills:
        return system_prompt, []

    # Normalise agent tool names for overlap check.
    agent_tools: set[str] = set()
    for raw in agent.tools or []:
        t = raw if isinstance(raw, str) else raw.get("name", "")
        if t:
            agent_tools.add(t.lower())

    qualifying: list[Any] = []
    for skill in all_skills:
        if skill.status != "approved":
            continue
        if not skill.instructions:
            continue
        # Tool-overlap check: skill.tools must share at least one tool with agent.
        # Exception: if skill.tools is empty, the skill is tool-agnostic → allow.
        skill_tools = skill.tools or []
        if skill_tools:
            skill_tool_names = {
                (t if isinstance(t, str) else t.get("name", "")).lower() for t in skill_tools if t
            }
            if not (skill_tool_names & agent_tools):
                continue
        qualifying.append(skill)
        if len(qualifying) >= _SKILL_MAX_COUNT:
            break

    if not qualifying:
        return system_prompt, []

    # Build injection block.
    skill_lines: list[str] = []
    injected: list[Any] = []
    for skill in qualifying:
        instructions = (skill.instructions or "").strip()
        # Truncate per-skill to cap.
        if len(instructions) > _SKILL_MAX_CHARS:
            instructions = instructions[:_SKILL_MAX_CHARS] + "…"
        skill_lines.append(f"### {skill.name} ({skill.slug})\n{instructions}")
        injected.append(skill)

    skills_block = "## Learned skills\n\n" + "\n\n".join(skill_lines)

    updated = (system_prompt + "\n\n" + skills_block) if system_prompt else skills_block

    # Update usage tracking — increment usage_count + last_used_at for each
    # injected skill.  Fail-safe: never propagate errors.
    try:
        now = datetime.now(UTC)
        for skill in injected:
            skill.usage_count = (skill.usage_count or 0) + 1
            skill.last_used_at = now
        await session.flush()
    except Exception:
        logger.warning(
            "skill_injection: usage tracking update failed for agent=%r — continuing",
            agent.agent_id,
            exc_info=True,
        )

    return updated, injected


def default_agent_instruction(agent_id: str, override: str | None = None) -> str:
    """Imperative user message that makes an agent act immediately with its tools.

    Autonomous (non-conversational) runs — pipeline ``agent_invocation`` nodes and
    the scout scheduler — must pass a directive message, or a small model just
    replies passively and calls no tools. Shared so both call sites stay in sync.
    A caller-supplied override always wins.

    Scout instruction includes an explicit note that MCP tools are pre-connected
    and never deferred. This is a prompt-level fallback for the primary fix
    (``MCP_CONNECTION_NONBLOCKING=false`` in the subprocess env) so scouts do not
    give up if they observe a deferred-tools hint in their session context.
    """
    if override:
        return override
    if agent_id.startswith("marketing.scout."):
        return (
            "Execute your scan NOW. Use your tools: call your fetch tools "
            "(e.g. news_api.search, state_doe.fetch, board_minutes.fetch) to pull "
            "current items from your sources, evaluate each against your allowed "
            "reason codes, and call signal_queue.write for EACH qualifying signal "
            "(one call per signal). Use reason_codes.get_allowlist if unsure which "
            "codes you may emit. When done, briefly report how many signals you "
            "emitted. If nothing qualifies this run, say so explicitly — do not ask "
            "for clarification; you are running autonomously.\n\n"
            "IMPORTANT — your Artemis MCP tools (prefixed mcp__artemis__) are "
            "pre-connected and fully available. If any tool appears as 'deferred' "
            "or 'not yet connected', call it anyway — the connection is synchronous "
            "and the tool will execute. Do NOT skip tool calls or report 0 signals "
            "solely because tools appear deferred.\n\n"
            "NEVER INVENT A SIGNAL. Every signal must come from an item a fetch "
            "tool actually returned to you in THIS run, and its source URL must be "
            "the exact URL that item carried. Do not construct, complete, guess or "
            "shorten a URL, and never write a plausible-sounding headline you did "
            "not read.\n\n"
            "Zero is a real and useful answer. If your feeds returned nothing this "
            "run, report zero signals — that is success, not failure. The line "
            "above about not reporting 0 means only this: do not report zero "
            "because a tool LOOKED unavailable without calling it. It never means "
            "you should produce signals to avoid an empty result. An empty scan "
            "costs nothing; one invented signal costs the credibility of every "
            "real one beside it."
        )
    if agent_id.startswith("marketing.qualifier."):
        return (
            "Process the pending signals NOW. Use your tools to read context and "
            "apply your qualification logic. Do not ask for clarification; act "
            "autonomously and report your result."
        )
    if agent_id.startswith("marketing.content."):
        return (
            "Assemble your deliverable NOW from the qualified inputs in context. "
            "Use your tools. Do not ask for clarification; act autonomously."
        )
    return (
        "Execute your task now using your available tools. "
        "Act autonomously; do not ask for clarification."
    )


async def run_agent(
    *,
    session: AsyncSession,
    agent_id: str,
    user_message: str | None = None,
    shared_context: dict[str, object] | None = None,
    owner_user_id: int | None = None,
    model_adapter: ModelAdapter | None = None,
    campaign_candidate_id: int | None = None,
) -> AgentRun:
    """Execute an agent definition and return the completed AgentRun.

    Args:
        session:        SQLAlchemy async session (caller owns commit/rollback).
        agent_id:       Slug of the agent definition to load.
        user_message:   Initial user message. Falls back to agent.goal if None.
        shared_context: Optional key/value dict injected into the system prompt
                        as a ``## Context`` block.
        owner_user_id:  User who triggered this run (for auditing).
        model_adapter:  Override the default AnthropicAdapter — pass a
                        FakeAdapter in tests so no real API calls are made.
        campaign_candidate_id: When this run is content drafting / brief work
                        for a specific campaign, the candidate id. Tagged onto
                        the cost_events row so the per-campaign rollup picks
                        it up. Optional; None for non-campaign runs.

    Returns:
        The AgentRun row after completion (status='completed') or failure
        (status='failed').
    """
    # Load agent definition first — its provider field drives adapter resolution
    agent = await get_agent(session, agent_id)

    # Resolve adapter: explicit override > provider cascade from agent row >
    # legacy AnthropicAdapter() as last resort.
    if model_adapter is not None:
        adapter = model_adapter
    else:
        from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

        agent_provider = getattr(agent, "provider", None)
        agent_fallback = getattr(agent, "fallback_provider", None)
        try:
            adapter = resolve_adapter(agent_provider, agent_fallback)
        except NoProviderAvailableError:
            logger.warning(
                "No provider in cascade resolved for agent %r; "
                "attempting AnthropicAdapter hard-fallback "
                "(raises MissingApiKeyError fast if ANTHROPIC_API_KEY is unset — "
                "caught by the outer exception handler, logged as failed run)",
                agent_id,
            )
            adapter = AnthropicAdapter()

    # Build system prompt (rich injection: persona, Josh-spec reason codes, state nuances, etc.)
    system_prompt = _build_system_prompt(agent, shared_context)

    # P5 — Inject approved skills (decision #3: tool-overlap, max 3, ~200 tok each).
    # Fail-safe: any error inside returns original prompt + no tracking update.
    system_prompt, _injected_skills = await _inject_skills_into_prompt(
        session, agent, system_prompt
    )

    # Choose the user message: explicit > agent.goal > generic
    effective_message: str = user_message or agent.goal or "Please proceed."

    # Create the AgentRun row
    run_id = str(uuid.uuid4())
    run = await create_agent_run(
        session,
        run_id=run_id,
        agent_id=agent_id,
        status="running",
        user_message=effective_message,
        shared_context=shared_context,
        owner_user_id=owner_user_id,
    )
    await session.flush()

    # Build per-call tool registry from agent.tools (list of name strings).
    # Unknown tool names are dropped with a single WARNING; they do not crash the run.
    tool_context = ToolContext(
        session=session,
        agent_id=agent_id,
        agent_db_id=agent.id,
        agent_run_id=run_id,
        pipeline_run_id=str(shared_context["pipeline_run_id"])
        if shared_context and "pipeline_run_id" in shared_context
        else None,
    )
    tool_registry = ToolRegistry()
    unknown_tools: list[str] = []
    for raw_name in agent.tools or []:
        name = raw_name if isinstance(raw_name, str) else raw_name.get("name", "")
        factory = get_factory(name)
        if factory is None:
            unknown_tools.append(name)
            continue
        tool_def, impl = factory(tool_context)
        tool_registry.register(tool_def, impl)
    if unknown_tools:
        logger.warning(
            "Agent %r declares unknown tools (dropped): %s. Known: %s",
            agent_id,
            unknown_tools,
            known_tool_names(),
        )

    # Broadcast run started
    await ws_manager.broadcast(
        run_id,
        agent_started_event(run_id, agent_id, effective_message).to_dict(),
    )

    # Build hook registry that streams events to WS subscribers
    hooks = _build_agent_hooks(run_id)

    # Initialize result to None so it is always defined post-try/except
    # (the except branch does not produce a RunResult).
    result: Any = None

    # OBS-2: latency for the agent_traces row below — mirrors chat.py's
    # _turn_start / elapsed_ms pattern for the conversational path.
    _run_start = start_timer()

    try:
        if _is_claude_code_tool_run(adapter, tool_registry):
            # claude-code IS the agent runtime for tool-using scouts: it spawns
            # the per-run Artemis MCP server and runs its own fetch→call-tool loop
            # on the user's subscription, returning a single final result. Signals
            # are written directly to the DB by that MCP-server process, so no
            # further artemis turns run here. (Anthropic/run_turn path unchanged.)
            from typing import cast

            from artemis.agent.client import CompletionRequest
            from artemis.agent.types import RunResult, StopReason
            from artemis.providers.claude_code.adapter import (
                ClaudeCodeAdapter,
                resolve_claude_config_dir,
            )

            cc_adapter = cast(ClaudeCodeAdapter, adapter)
            _cc_timeout, _cc_max_turns = _content_node_timeout_and_turns(agent_id)
            completion = await cc_adapter.run_with_tools(
                CompletionRequest(
                    messages=[_user_msg(effective_message)],
                    system=system_prompt,
                    model=agent.model,
                ),
                agent_id=agent_id,
                run_id=run_id,
                pipeline_run_id=tool_context.pipeline_run_id,
                agent_tools=[t.name for t in tool_registry.specs()],
                timeout_seconds=_cc_timeout,
                max_turns=_cc_max_turns,
                claude_config_dir=resolve_claude_config_dir(agent_id),
            )
            # Normalise the single CompletionResponse into the RunResult shape the
            # downstream text/usage/finalize code expects (one assistant message).
            # OBS-2: thread completion.tool_calls through metadata the same way
            # artemis/agent/loop.py::run_turn does, so collect_tools_used() below
            # picks it up identically regardless of which branch produced `result`.
            result = RunResult(
                messages=[_user_msg(effective_message), completion.message],
                stop_reason=cast(StopReason, completion.stop_reason),
                usage=completion.usage,
                iterations=1,
                metadata={"tool_calls": completion.tool_calls} if completion.tool_calls else {},
            )
        else:
            result = await run_turn(
                adapter=adapter,
                messages=[_user_msg(effective_message)],
                system=system_prompt,
                model=agent.model,
                max_iterations=agent.max_iterations,
                tools=tool_registry if len(tool_registry) > 0 else None,
                hooks=hooks,
            )

        # Extract final assistant text
        final_text = _extract_text(result)

        # Persist the response into agent_context
        await set_agent_context(session, run_id, "final_response", final_text)

        # Finalise the run row
        run = await set_agent_run_completed(
            session,
            run_id,
            status="completed",
            cost_input_tokens=result.usage.input_tokens,
            cost_output_tokens=result.usage.output_tokens,
        )

        # Record cost event — never propagate failures.
        # Uses adapter_identity for the resolved adapter's real provider/model/path
        # (the legacy code derived these from the agent row, which goes stale when
        # the resolver falls through the cascade). Tags campaign_candidate_id when
        # this run is content drafting for a specific campaign.
        try:
            from artemis.costs.events import adapter_identity

            _provider, _adapter_model, _path = adapter_identity(adapter)
            # Prefer the agent's configured model (it's what the LLM actually
            # billed against). Fall back to the adapter's default if missing.
            _cost_model = agent.model or _adapter_model
            await record_cost_event(
                session,
                provider=_provider,
                model=_cost_model,
                provider_path=_path,
                feature_tag="agent_run",
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                source_kind="agent_run",
                source_id=str(run.id),
                agent_id=agent.id,
                campaign_candidate_id=campaign_candidate_id,
            )
        except Exception:
            logger.warning("cost_event recording failed for run_id=%s", run_id, exc_info=True)

        # OBS-2: agent_traces row for the pipeline/builder agent path — the other
        # half of OBS-1, which only ever wired capture_trace() into the
        # conversational path (artemis/floating_artemis/chat.py). Written
        # synchronously via record_trace() (not the fire-and-forget capture_trace())
        # and flushed into the SAME session/transaction this function already
        # commits below: a real pipeline run executes via
        # artemis/pipelines/run_cli.py's `asyncio.run(...)`, which cancels any
        # still-pending background task the moment the coroutine returns, so a
        # fire-and-forget write scheduled on the LAST node of a run risks being
        # cancelled before it ever reaches Postgres — silently losing exactly the
        # observability this is meant to add. Own try/except so a trace-write
        # failure can never fail an otherwise-successful agent run.
        try:
            from artemis.costs.events import adapter_identity as _trace_adapter_identity

            _trace_provider, _trace_adapter_model, _ = _trace_adapter_identity(adapter)
            _trace_model = agent.model or _trace_adapter_model
            await record_trace(
                session,
                agent_id=agent_id,
                feature_tag="agent_run",
                session_id=run_id,
                provider=_trace_provider,
                model=_trace_model,
                input_summary=effective_message[:500] if effective_message else None,
                tools_used=collect_tools_used(result),
                output_summary=final_text[:500] if final_text else None,
                outcome="success",
                latency_ms=elapsed_ms(_run_start),
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                owner_user_id=owner_user_id,
            )
        except Exception:
            logger.warning("trace capture failed for run_id=%s", run_id, exc_info=True)

    except Exception as exc:  # noqa: BLE001
        logger.exception("Agent run '%s' failed", run_id)
        error_msg = f"{type(exc).__name__}: {exc}"
        await ws_manager.broadcast(run_id, agent_failed_event(run_id, error_msg).to_dict())
        run = await set_agent_run_completed(
            session,
            run_id,
            status="failed",
            error=error_msg,
        )
        # Record error cost event — lossless even on failure
        try:
            from artemis.costs.events import adapter_identity

            _provider, _adapter_model, _path = adapter_identity(adapter)
            _cost_model = agent.model or _adapter_model
            await record_cost_event(
                session,
                provider=_provider,
                model=_cost_model,
                provider_path=_path,
                feature_tag="agent_run",
                source_kind="agent_run",
                source_id=str(run.id),
                agent_id=agent.id,
                campaign_candidate_id=campaign_candidate_id,
                is_error=True,
                error_kind=type(exc).__name__,
            )
        except Exception:
            logger.warning("cost_event error recording failed for run_id=%s", run_id, exc_info=True)

        # OBS-2: error trace row — lossless recording, mirrors the error cost
        # event immediately above. `result` may still be None here (the
        # failure can happen before run_with_tools/run_turn ever returns), so
        # tools_used can only reflect calls made before the failure when a
        # partial `result` exists.
        try:
            from artemis.costs.events import adapter_identity as _trace_adapter_identity

            _trace_provider, _trace_adapter_model, _ = _trace_adapter_identity(adapter)
            _trace_model = agent.model or _trace_adapter_model
            await record_trace(
                session,
                agent_id=agent_id,
                feature_tag="agent_run",
                session_id=run_id,
                provider=_trace_provider,
                model=_trace_model,
                input_summary=effective_message[:500] if effective_message else None,
                tools_used=collect_tools_used(result) if result is not None else [],
                outcome="error",
                error=error_msg,
                latency_ms=elapsed_ms(_run_start),
                input_tokens=result.usage.input_tokens if result is not None else None,
                output_tokens=result.usage.output_tokens if result is not None else None,
                owner_user_id=owner_user_id,
            )
        except Exception:
            logger.warning("error trace capture failed for run_id=%s", run_id, exc_info=True)

    await session.flush()

    # CC14: commit the agent_run row NOW so it is globally visible before
    # summarize_async fires.  The pipeline executor uses one long transaction
    # across all nodes (flush-per-node, single commit at end in routes.py).
    # The summarizer opens its OWN session — it cannot see rows that are only
    # flushed but not committed in the caller's session, causing FK violations.
    # Committing here is safe: run_agent is called with the caller's session
    # but the caller (execute_agent_node / PipelineExecutor) only reads
    # in-memory fields from the returned AgentRun object after this point,
    # never relying on these rows being part of the outer transaction.
    await session.commit()

    # Fire-and-forget trajectory summary — does not block or affect run status.
    # CC13: pass a snapshot built from the in-scope run object (already flushed
    # into this session) rather than just run.id.  The background task's new
    # session cannot see the unflushed row, so we pass the data directly and
    # eliminate the DB lookup entirely.
    # CC14: the commit above ensures the FK target (agent_runs.id) is visible
    # to the summarizer's separate session before it attempts the INSERT.
    # CC16: enrich snapshot with tool calls, signal count, final text, duration.
    #   The signals_emitted query is safe post-commit (CC14 ensures agent_run is
    #   committed; signal_queue rows written during the run are also committed
    #   because run_agent's session.commit() flushes the full transaction).
    # CC17: query tool_invocations for the real MCP-path tool calls first;
    #   fall back to message-walking if empty (preserves CC16 for anthropic
    #   in-process path which never writes to tool_invocations).
    from sqlalchemy import func, select

    from artemis.builder.trajectory_summarizer import summarize_async
    from artemis.marketing.models import SignalQueue
    from artemis.tools.models import ToolInvocation

    # Count signals written by this run (provenance->>'agent_run_id' == run_id).
    # Uses JSONB text-extraction operator (->>'agent_run_id') via SQLAlchemy's
    # [] subscript + .as_string(). Safe post-commit because run_agent's
    # session.commit() above flushed both agent_runs and signal_queue rows.
    sig_result = await session.execute(
        select(func.count())
        .select_from(SignalQueue)
        .where(SignalQueue.provenance["agent_run_id"].as_string() == run.run_id)
    )
    signals_emitted: int = sig_result.scalar_one() or 0

    # CC17: fetch MCP-path tool invocations committed by mcp_server subprocess.
    inv_result = await session.execute(
        select(ToolInvocation)
        .where(ToolInvocation.agent_run_id == run.run_id)
        .order_by(ToolInvocation.invoked_at)
    )
    mcp_invocations: list[Any] = list(inv_result.scalars().all())

    snapshot = _build_snapshot(run, result, signals_emitted, mcp_invocations)
    await summarize_async(snapshot)

    return run


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_snapshot(
    run: Any,
    result: Any,
    signals_emitted: int,
    mcp_invocations: list[Any] | None = None,  # ToolInvocation rows from CC17 table
) -> Any:  # returns AgentRunSnapshot; typed as Any to avoid cross-module import at module level
    """Build an enriched AgentRunSnapshot from the completed run + RunResult.

    CC17: extraction strategy — query tool_invocations first (MCP path); fall
    back to message-walking if empty (anthropic in-process path, CC16).

    The two paths are mutually exclusive in practice:
    - claude-code runs write to tool_invocations via mcp_server; result.messages
      contains only the final assistant text (tool blocks happen inside the
      subprocess).
    - anthropic/run_turn runs never write to tool_invocations; result.messages
      contains full ToolUseBlock / ToolResultBlock pairs.

    Keeping both paths means neither provider regresses.

    Parameters
    ----------
    run:
        The AgentRun ORM object after set_agent_run_completed. Provides
        run_id, id, agent_id, status, user_message, error, started_at,
        completed_at.
    result:
        The RunResult from run_turn / ClaudeCodeAdapter. Provides messages
        with ToolUseBlock / ToolResultBlock / TextBlock content.
    signals_emitted:
        Pre-counted number of signal_queue rows attributed to this run.
        Caller is responsible for querying this AFTER commit (CC14 safe).
    mcp_invocations:
        List of ToolInvocation ORM rows for this run_id (CC17).  When
        non-empty, these are used as the authoritative tool_calls source and
        message-walking is skipped.  When empty or None, falls back to
        message-walking for the anthropic in-process path.

    Returns
    -------
    AgentRunSnapshot
        Frozen snapshot ready to pass to summarize_async().
    """
    from artemis.agent.types import TextBlock, ToolResultBlock, ToolUseBlock
    from artemis.builder.trajectory_summarizer import AgentRunSnapshot, _ToolCallSummary

    messages = result.messages if result is not None else []

    tool_calls_list: list[_ToolCallSummary] = []

    if mcp_invocations:
        # CC17: MCP path — use ground-truth invocation log.
        for inv in mcp_invocations:
            tool_calls_list.append(
                _ToolCallSummary(
                    name=inv.tool_name,
                    success=inv.success,
                    result_preview=inv.result_preview or "",
                )
            )
    else:
        # CC16 fallback: anthropic in-process path — walk result.messages.
        # Build a map from tool_use_id → ToolResultBlock first, then walk in order.
        result_map: dict[str, ToolResultBlock] = {}
        for msg in messages:
            if msg.role == "user":
                for block in msg.content:
                    if isinstance(block, ToolResultBlock):
                        result_map[block.tool_use_id] = block

        for msg in messages:
            if msg.role == "assistant":
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        result_block = result_map.get(block.id)
                        if result_block is not None:
                            success = not result_block.is_error
                            preview = (result_block.content or "")[:100]
                        else:
                            # Tool call with no result (end_turn before tool result)
                            success = True
                            preview = ""
                        tool_calls_list.append(
                            _ToolCallSummary(
                                name=block.name,
                                success=success,
                                result_preview=preview,
                            )
                        )

    # --- Extract final assistant text (last assistant message, ~500 chars) ---
    final_text: str | None = None
    for msg in reversed(messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                raw = " ".join(texts)
                final_text = raw[:500] if len(raw) > 500 else raw
            break

    # --- Compute duration_ms --------------------------------------------------
    duration_ms: int | None = None
    started_at = getattr(run, "started_at", None)
    completed_at = getattr(run, "completed_at", None)
    if started_at is not None and completed_at is not None:
        delta = completed_at - started_at
        duration_ms = int(delta.total_seconds() * 1000)

    return AgentRunSnapshot(
        run_id=run.run_id,
        run_pk=run.id,
        agent_id=run.agent_id,
        status=run.status,
        user_message=run.user_message,
        error=run.error,
        tool_calls=tuple(tool_calls_list),
        signals_emitted=signals_emitted,
        final_text=final_text,
        duration_ms=duration_ms,
    )


def _is_claude_code_tool_run(adapter: ModelAdapter, tool_registry: ToolRegistry) -> bool:
    """True when claude-code should run its OWN tool loop instead of run_turn.

    Detection is ``isinstance(adapter, ClaudeCodeAdapter)``: ``resolve_adapter``
    returns a *bare* adapter instance (not a cascade wrapper — the cascade in
    ``artemis/providers/resolver.py`` returns the first constructible adapter
    directly), and the ``model_adapter`` override path passes an instance too. A
    duck-typed ``provider`` string check would miss the override and the resolver
    both return concrete instances, so isinstance is the precise test. Only fires
    when the agent actually has tools — a no-tool claude-code run stays on the
    text/``run_turn`` path.
    """
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    return isinstance(adapter, ClaudeCodeAdapter) and len(tool_registry) > 0


def _content_node_timeout_and_turns(agent_id: str) -> tuple[float | None, int | None]:
    """Return (timeout_seconds, max_turns) overrides for content-class agents.

    Content agents (marketing.content.*) make exactly ONE tool call per run:
    - marketing.content.asset_selector: list_approved_assets → done (≤30s)
    - marketing.content.writing_studio_adapter: enqueue → done (≤60s)

    A 120s wall-clock timeout (6× the observed maximum of ~53s) is tight enough to
    fail-fast on a hung subprocess while leaving ample headroom for a slow model
    response.  ``max_turns=5`` bounds the internal claude-code agent loop so a
    stuck tool-use loop cannot run indefinitely.

    Scout agents (marketing.scout.*) remain unbounded — they call multiple tools in
    sequence and the default 900s timeout is appropriate for their workload.

    Returns (None, None) for all other agent classes (no override).
    """
    import os

    if agent_id.startswith("marketing.content."):
        raw = os.environ.get("ARTEMIS_CONTENT_NODE_TIMEOUT_SECONDS")
        timeout: float | None = None
        if raw:
            import contextlib

            with contextlib.suppress(ValueError):
                timeout = float(raw)
        if timeout is None:
            timeout = 120.0
        return timeout, 5
    return None, None


def _user_msg(text: str):  # type: ignore[no-untyped-def]
    from artemis.agent.loop import user_message as _make

    return _make(text)


def _extract_text(result) -> str:  # type: ignore[no-untyped-def]
    """Pull plain text from the last assistant message in *result*."""
    from artemis.agent.types import TextBlock

    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                return " ".join(texts)
    return ""
