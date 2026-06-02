"""Agent-Builder — conversational agent definition designer (O1).

This is the first surface that uses the Builder-Engine. Its job:
  1. Accept a user's natural-language description of what they want an agent to do.
  2. Ask clarifying questions across 3-5 turns.
  3. Generate a draft agent definition (system prompt, tools, model).
  4. Propose co-requisite Skills when the agent implies missing capabilities.
  5. Optionally fire a test run (pending Lead Decision 2).
  6. Commit the definition to the agents table.

NOTE: The coupling between the Builder-Engine primitives and this class is the
subject of Lead Consult 1. This implementation is a STUB — it contains the
system prompt, tool list, and conversation handler skeleton, but the actual
Builder-Engine integration is held pending Consult 1.

See also:
  engine.py       — the primitives this builder calls as tools
  routes.py       — HTTP routes for /api/builder/sessions/*
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

from artemis.agent.tools import ToolRegistry
from artemis.agent.types import Message, TextBlock

logger = logging.getLogger(__name__)

# ── Streaming event types ──────────────────────────────────────────────────────

BuilderEventType = Literal[
    "turn_start",
    "tool_call",
    "tool_result",
    "assistant_token",
    "proposal_staged",
    "heartbeat",
    "turn_complete",
    "error",
]


@dataclass(slots=True)
class BuilderEvent:
    type: BuilderEventType
    payload: dict[str, Any]

    def to_sse(self) -> str:
        return f"event: {self.type}\ndata: {json.dumps(self.payload)}\n\n"


# ── System prompt ─────────────────────────────────────────────────────────────

AGENT_BUILDER_SYSTEM_PROMPT = """\
You are the Agent-Builder — a senior engineer embedded in Artemis OS.
Your job is to design agent definitions through conversation.

When a user describes a problem or task they want automated:
1. Identify the core goal and any implicit sub-goals.
2. Ask 3-5 focused clarifying questions covering:
   - Audience and output format
   - Trigger/schedule (one-shot, recurring, event-driven)
   - Data sources and required integrations
   - Voice/tone (if the agent writes text for humans)
   - Edge cases or constraints you've spotted
3. Generate a draft definition with:
   - name: a slug-safe identifier (kebab-case)
   - goal: one clear sentence
   - system_prompt: 100-200 words tuned to the user's context
   - tools: a list of tool names the agent needs (be specific)
   - model: the right model for the task (default claude-sonnet-4-6)
   - trigger: when/how this should run (describe for the Automation surface)
4. Call propose() with the draft. Show the user the definition clearly.
5. Identify any tools the draft needs that don't exist as Skills.
   For each missing capability, call propose() with kind="skill" for a co-proposal.
6. Ask: "Want me to test-run this against real data before saving?"

If the user is opening an existing agent (edit session):
- Start by calling read_recent_runs() to load trajectory summaries.
- Lead with: "I've reviewed your last N runs. Here's what I noticed: [patterns]."
- After read_recent_runs(), BEFORE calling propose():
  - Call read_tool_signatures(agent_id) to load the actual parameter schemas +
    allowed enum values for every tool the agent uses.
  - For any tool that writes to a DB table referenced by your proposed system prompt,
    call read_db_schema with that table name.
  - For any skill you intend to co-propose, call read_skill_catalog to confirm the
    name isn't already taken.
  - **Call search_memory(agent_id) to retrieve curated observations across the
    agent's full history (M2). Recent runs show only the latest N; search_memory
    surfaces durable patterns from all runs. Treat memory observations as more
    authoritative than trajectory summaries — they have evidence chains and may
    supersede stale patterns.**
- NEVER enumerate enum values, status names, or parameter constraints from inference.
  ALWAYS read them via the grounding tools first.
- If a grounding tool returns data that contradicts your proposed change, revise
  BEFORE calling propose().
- Then propose definition changes, citing specific run IDs.

## Tool use rules
- Call read_existing() before proposing to avoid duplicating existing agents/skills.
- Call read_capabilities() once at the start of a new session.
- Never call commit() directly — the HTTP approve endpoint owns commit.
- Call test_run() after producing a draft to validate it — show the user the output.
  Note: test_run uses read-only tool stubs; tools_skipped lists what was blocked.

## Style rules
- Ask one question at a time when clarifying — do not dump all questions at once.
  (Exception: if all 3-5 questions are closely related and the user would prefer
  to answer them in one shot, you may list them together clearly.)
- Show the draft definition in a clean, readable format — not raw JSON.
- When proposing Skills, explain why the agent needs them.
- Be concise. The user is building something, not reading a manual.
"""

# ── Tool list (bound to Builder-Engine primitives at session init time) ────────

AGENT_BUILDER_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_existing",
        "description": "List existing definitions of a given kind (agent/skill/workflow).",
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["agent", "skill", "workflow"],
                    "description": "The kind of definition to list.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of results (default 50).",
                    "default": 50,
                },
            },
            "required": ["kind"],
        },
    },
    {
        "name": "read_capabilities",
        "description": "Return available providers, models, and integrations.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "read_recent_runs",
        "description": (
            "Return the most recent agent runs + trajectory summaries. "
            "Use in edit sessions to surface self-improvement context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent_id string of the agent to look up.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max runs to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "propose",
        "description": (
            "Stage a draft definition as a DefinitionProposal awaiting user approval. "
            "Use for both agent definitions and co-proposed skills."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["agent", "skill", "workflow", "automation"],
                    "description": "Kind of definition being proposed.",
                },
                "definition": {
                    "type": "object",
                    "description": "The draft definition object.",
                },
                "target_id": {
                    "type": "integer",
                    "description": "Non-null when revising an existing definition.",
                },
                "citations": {
                    "type": "object",
                    "description": (
                        "Self-improvement citations. Shape: "
                        "{run_ids: [int, ...], "
                        "observations: [{run_id, what_stalled, what_was_missing, what_worked}], "
                        "summary: str, pattern_label: str|null}. "
                        "run_ids MUST be IDs returned by read_recent_runs in this session — "
                        "never fabricate or guess IDs."
                    ),
                },
            },
            "required": ["kind", "definition"],
        },
    },
    {
        "name": "test_run",
        "description": (
            "Fire a sandboxed trial run of the current draft definition against a test prompt. "
            "Read-only tools only. Returns output + tools_skipped list. "
            "Show the user the output and tools_skipped before asking if they want to proceed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "definition": {
                    "type": "object",
                    "description": "The draft agent definition to test.",
                },
                "prompt": {
                    "type": "string",
                    "description": "The test prompt to run against the draft.",
                },
            },
            "required": ["definition", "prompt"],
        },
    },
    # ── CC20 grounding tools ──────────────────────────────────────────────────
    {
        "name": "read_tool_signatures",
        "description": (
            "Return the actual parameter schemas + valid enum values for all tools "
            "an agent has access to. MUST be called after read_recent_runs() and "
            "BEFORE propose() when editing an existing agent."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent_id string (dotted slug) of the agent to inspect.",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "read_db_schema",
        "description": (
            "Return the actual DB schema (columns, CHECK constraints, FK relationships) "
            "for the requested tables. Use before proposing changes that reference DB "
            "column names or status values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Table names to inspect.",
                },
            },
            "required": ["table_names"],
        },
    },
    {
        "name": "read_skill_catalog",
        "description": (
            "List ALL registered tools across the platform plus all skills table rows. "
            "Use before co-proposing a skill to confirm the name is not already taken."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Optional filter: 'tool' or 'skill' (default: both).",
                },
            },
            "required": [],
        },
    },
    # ── M2 memory retrieval tool ────────────────────────────────────────────────
    {
        "name": "search_memory",
        "description": (
            "Retrieve curated memory observations for an agent across its full run history. "
            "Returns observations ranked by recency, relevance, and evidence chain quality. "
            "MUST be called after read_recent_runs() and BEFORE propose() in edit sessions. "
            "Observations are more authoritative than trajectory summaries — they have "
            "evidence chains and are deduplicated across all runs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent_id string (dotted slug) to search memory for.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional natural-language query to focus retrieval.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max observations to return (default 10, max 50).",
                    "default": 10,
                },
            },
            "required": ["agent_id"],
        },
    },
]


# ── Conversation handler ───────────────────────────────────────────────────────


def build_tool_registry(*, db_session: Any, builder_session_id: int) -> ToolRegistry:
    """Build the ToolRegistry for an Agent-Builder session.

    Binds the engine primitives with closures that capture the db_session and
    builder_session_id so the builder's tool calls are scoped to this session.

    NOTE: This is the integration point that Decision 1 affects.
    If Builder-Engine and Agent-Builder are fully decoupled, this factory
    lives in engine.py and is generic. If they're entwined, the builder_session_id
    context is injected here and the engine primitives are thin wrappers.
    Current implementation is a forward reference — wired after Consult 1.
    """
    from artemis.agent.types import Tool
    from artemis.builder import engine

    registry = ToolRegistry()

    # Tracks run PKs returned by read_recent_runs so _propose can validate
    # that the LLM is not fabricating run_ids it never saw.
    _seen_run_ids: set[int] = set()

    async def _read_existing(inp: dict[str, Any]) -> str:
        import json

        results = await engine.read_existing(
            inp["kind"], db_session=db_session, limit=inp.get("limit", 50)
        )
        return json.dumps(results, indent=2)

    async def _read_capabilities(inp: dict[str, Any]) -> str:
        import json

        result = await engine.read_capabilities(db_session=db_session)
        return json.dumps(result, indent=2)

    async def _read_recent_runs(inp: dict[str, Any]) -> str:
        import json

        result = await engine.read_recent_runs(
            inp["agent_id"], db_session=db_session, limit=inp.get("limit", 10)
        )
        # Record the PKs returned so _propose can validate membership.
        for entry in result:
            if "id" in entry:
                _seen_run_ids.add(int(entry["id"]))
        return json.dumps(result, indent=2)

    async def _propose(inp: dict[str, Any]) -> str:
        import json

        citations = inp.get("citations")
        if citations:
            cited_ids = [int(r) for r in citations.get("run_ids", [])]
            bad_ids = [rid for rid in cited_ids if rid not in _seen_run_ids]
            if bad_ids:
                return json.dumps(
                    {
                        "error": (
                            "run_ids validation failed: the following IDs were not returned by "
                            f"read_recent_runs in this session and cannot be cited: {bad_ids}. "
                            "Only reference run IDs from the set read_recent_runs returned."
                        )
                    }
                )

        proposal_id = await engine.propose(
            inp["kind"],
            inp["definition"],
            db_session=db_session,
            builder_session_id=builder_session_id,
            target_id=inp.get("target_id"),
            proposed_by="builder",
            citations=citations,
        )
        await db_session.commit()
        return json.dumps({"proposal_id": proposal_id, "status": "pending"})

    async def _test_run(inp: dict[str, Any]) -> str:
        import json

        from artemis.providers import get_adapter
        from artemis.providers.errors import MissingApiKeyError, UnknownProviderError

        _adapter = None
        for _candidate in ("claude-code", "codex", "lm-studio", "anthropic"):
            try:
                _adapter = get_adapter(_candidate)
                break
            except (MissingApiKeyError, UnknownProviderError):
                continue
            except Exception:
                continue
        if _adapter is None:
            return json.dumps({"error": "No LLM provider available for test_run."})

        result = await engine.sandbox_run(
            inp["definition"],
            inp["prompt"],
            adapter=_adapter,
            allow_writes=False,
        )
        return json.dumps(result, indent=2)

    # ── CC20 grounding tools (in-process path for non-ClaudeCode adapters) ────

    async def _read_tool_signatures(inp: dict[str, Any]) -> str:
        import json

        from artemis.builder.grounding import extract_allowed_status_values
        from artemis.builders import repository as _br

        agent_id_arg = str(inp.get("agent_id", ""))
        if not agent_id_arg:
            return json.dumps({"error": "agent_id is required"})

        try:
            agent_row = await _br.get_agent(db_session, agent_id_arg)
        except ValueError:
            return json.dumps({"error": f"Agent not found: {agent_id_arg!r}"})

        from artemis.tools.registry import get_factory

        allowed_statuses = await extract_allowed_status_values(db_session)
        declared = agent_row.tools or []
        tool_entries: list[dict[str, Any]] = []
        for t_entry in declared:
            t_name = t_entry if isinstance(t_entry, str) else str(t_entry.get("name", ""))
            te: dict[str, Any] = {"name": t_name}
            factory = get_factory(t_name)
            if factory is not None:
                try:
                    from unittest.mock import MagicMock

                    stub = MagicMock()
                    stub.session = None
                    stub.agent_id = "__grounding_stub__"
                    stub.agent_db_id = 0
                    stub.agent_run_id = "__grounding_stub__"
                    stub.pipeline_run_id = None
                    td, _ = factory(stub)
                    te["description"] = td.description
                    te["input_schema"] = td.input_schema
                except Exception:
                    pass
            if "status" in t_name.lower() or "queue" in t_name.lower():
                te["allowed_status_values"] = allowed_statuses
            tool_entries.append(te)

        return json.dumps({"agent_id": agent_id_arg, "tools": tool_entries}, indent=2)

    async def _read_db_schema(inp: dict[str, Any]) -> str:
        import json

        from artemis.builder.grounding import extract_db_constraints

        raw = inp.get("table_names", [])
        if not isinstance(raw, list):
            return json.dumps({"error": "table_names must be an array of strings"})
        table_names = [str(t) for t in raw]
        if not table_names:
            return json.dumps({"error": "table_names must be a non-empty array"})

        result = await extract_db_constraints(db_session, table_names)
        return json.dumps(result, indent=2)

    async def _read_skill_catalog(inp: dict[str, Any]) -> str:
        import json

        from artemis.builder.grounding import extract_tool_registry

        kind_filter = str(inp.get("kind", "")) if inp.get("kind") else None
        catalog = await extract_tool_registry(db_session)

        if kind_filter == "tool":
            return json.dumps(
                {"registered_tools": catalog["registered_tools"], "skills": []}, indent=2
            )
        if kind_filter == "skill":
            return json.dumps({"registered_tools": [], "skills": catalog["skills"]}, indent=2)
        return json.dumps(catalog, indent=2)

    async def _search_memory(inp: dict[str, Any]) -> str:
        """M2: retrieve curated memory observations for an agent (in-process path)."""
        import json

        from sqlalchemy import select as _sa_select

        from artemis.memory.models import MemoryScope
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope
        from artemis.memory.store import list_evidence_for_observation

        agent_id_arg = str(inp.get("agent_id", "")).strip()
        if not agent_id_arg:
            return json.dumps({"error": "agent_id is required"})

        query_arg = str(inp.get("query", "")).strip()
        limit_raw = inp.get("limit", 10)
        limit_arg = min(int(limit_raw) if limit_raw is not None else 10, 50)

        # Check whether the scope exists; if not, return [] (not error).
        scope_check = await db_session.execute(
            _sa_select(MemoryScope).where(
                MemoryScope.scope_kind == "agent",
                MemoryScope.scope_id == agent_id_arg,
            )
        )
        if scope_check.scalar_one_or_none() is None:
            return json.dumps([])

        scope = Scope(scope_kind="agent", scope_id=agent_id_arg)
        effective_query = query_arg or agent_id_arg
        observations = await search_observations(
            session=db_session,
            scope_set=[scope],
            query=effective_query,
            limit=limit_arg,
        )

        results: list[dict[str, Any]] = []
        for obs in observations:
            evidence_links = await list_evidence_for_observation(db_session, obs.id)
            evidence_summary = [
                {
                    "source_kind": ev.source_kind,
                    "source_id": ev.source_id,
                    "preview": ev.source_quote or "",
                }
                for ev in evidence_links[:3]
            ]
            results.append(
                {
                    "id": obs.id,
                    "content": obs.content,
                    "created_at": obs.created_at.isoformat(),
                    "confidence": obs.confidence,
                    "superseded_by": obs.superseded_by,
                    "evidence_summary": evidence_summary,
                }
            )

        return json.dumps(results, indent=2)

    impl_map = {
        "read_existing": _read_existing,
        "read_capabilities": _read_capabilities,
        "read_recent_runs": _read_recent_runs,
        "propose": _propose,
        "test_run": _test_run,
        # CC20 grounding.
        "read_tool_signatures": _read_tool_signatures,
        "read_db_schema": _read_db_schema,
        "read_skill_catalog": _read_skill_catalog,
        # M2 memory retrieval.
        "search_memory": _search_memory,
    }

    # CC21: wrap each in-process Builder tool impl so every call logs a
    # tool_invocations row scoped to builder_session_id.  The MCP-path
    # equivalent is in tools/mcp_server.py::_build_builder_server.
    from artemis.tools.mcp_server import _log_invocation, _summarize_args

    _failure_prefixes = (
        "VALIDATION_ERROR",
        "PERMISSION_DENIED",
        "STUB:",
        "TOOL_ERROR:",
        "UNKNOWN_TOOL:",
    )

    from artemis.agent.types import ToolImpl

    def _logged(tool_name: str, impl: ToolImpl) -> ToolImpl:
        async def _wrapped(inp: dict[str, Any]) -> str:
            args_summary = _summarize_args(inp)
            try:
                result: str = await impl(inp)
            except Exception as exc:
                await _log_invocation(
                    db_session,
                    builder_session_id=builder_session_id,
                    tool_name=tool_name,
                    args_summary=args_summary,
                    result_preview=f"EXCEPTION: {exc!s}"[:500],
                    success=False,
                )
                raise
            success = not any(result.startswith(p) for p in _failure_prefixes)
            result_preview = result[:500] if isinstance(result, str) else None
            await _log_invocation(
                db_session,
                builder_session_id=builder_session_id,
                tool_name=tool_name,
                args_summary=args_summary,
                result_preview=result_preview,
                success=success,
            )
            return result

        return _wrapped

    for spec_dict in AGENT_BUILDER_TOOL_SPECS:
        tool = Tool(
            name=spec_dict["name"],
            description=spec_dict["description"],
            input_schema=spec_dict["input_schema"],
        )
        registry.register(tool, _logged(spec_dict["name"], impl_map[spec_dict["name"]]))

    return registry


async def build_edit_session_opener(
    target_id: int,
    *,
    db_session: Any,
) -> str | None:
    """Return the opener text for an edit session on an existing agent.

    Loads builder-context (recent runs + trajectory summaries) and returns a
    plain-English summary the builder prepends to its first-turn system context.
    Returns None if the agent has no runs yet.

    M2: after the H3 trajectory-summary block, injects a "Prior observations"
    section sourced from search_observations (memory keystone).
    """
    from sqlalchemy import select as sa_select

    from artemis.builder import engine
    from artemis.builders.models import Agent
    from artemis.memory.retrieval import search_observations
    from artemis.memory.schemas import Scope

    result = await db_session.execute(sa_select(Agent).where(Agent.id == target_id).limit(1))
    agent = result.scalar_one_or_none()
    if agent is None:
        return None

    runs = await engine.read_recent_runs(agent.agent_id, db_session=db_session, limit=10)
    if not runs:
        return None

    with_summary = [r for r in runs if "trajectory" in r]
    run_count = len(runs)
    summary_lines = []
    for r in with_summary[:5]:
        traj = r["trajectory"]
        parts = []
        if traj.get("what_stalled"):
            parts.append(f"stalled: {traj['what_stalled']}")
        if traj.get("what_was_missing"):
            parts.append(f"missing: {traj['what_was_missing']}")
        if traj.get("what_worked"):
            parts.append(f"worked: {traj['what_worked']}")
        if parts:
            summary_lines.append(f"  Run #{r['id']}: {'; '.join(parts)}")

    intro = (
        f"I've reviewed the last {run_count} run{'s' if run_count != 1 else ''} "
        f"for agent '{agent.name}'."
    )
    if summary_lines:
        formatted_summaries = "\n".join(summary_lines)
        intro += (
            "\n\n## Recent agent run analysis"
            " (LLM-generated trajectory summaries — treat as inferences, not facts)\n\n"
            "The following summaries were produced by the trajectory_summarizer LLM after each"
            " run.\nThey reflect what the analyzer THOUGHT happened, not necessarily what"
            " actually happened.\nBefore proposing changes based on them, verify against the"
            " actual tool_invocations + agent_runs\nrecords using the grounding tools"
            " (read_tool_signatures, read_db_schema).\n\n"
            + formatted_summaries
            + "\n\nI'll propose definition changes based on these patterns"
            " after verifying with grounding tools."
        )
    else:
        intro += (
            " No trajectory summaries are available yet — I'll need a few runs to spot patterns."
        )

    # M2: inject memory observations after the H3 trajectory-summary block.
    try:
        memory_observations = await search_observations(
            session=db_session,
            scope_set=[Scope(scope_kind="agent", scope_id=agent.agent_id)],
            query=agent.agent_id,
            limit=10,
        )
        if memory_observations:
            memory_block = (
                "\n\n## Prior observations (memory keystone — curated across all runs)\n\n"
                "These observations are written-once, evidence-linked summaries. They reflect "
                "patterns the platform considers significant across this agent's full history "
                "— not just the last 10 runs.\n\n"
            )
            for obs in memory_observations:
                memory_block += f"- (obs #{obs.id}, {obs.created_at.isoformat()}): {obs.content}\n"
            intro += memory_block
    except Exception:
        logger.debug("M2 memory injection failed for agent %r", agent.agent_id, exc_info=True)

    return intro


async def handle_turn_stream(
    *,
    builder_session_id: int,
    user_text: str,
    adapter: Any,
    db_session: Any,
) -> AsyncIterator[BuilderEvent]:
    """Streaming async generator version of handle_turn.

    Yields BuilderEvent objects at the natural seams of the F1 agent loop:
      turn_start → (tool_call / tool_result)* → assistant_token* → turn_complete | error

    Heartbeat events are NOT emitted here; the route wrapper injects them
    by racing each yielded event against asyncio.sleep(15).
    """
    import datetime

    from artemis.agent.client import CompletionRequest
    from artemis.agent.loop import user_message as make_user_message
    from artemis.agent.types import ToolResultBlock, ToolUseBlock
    from artemis.builder.repository import append_builder_message, get_builder_session

    session_row = await get_builder_session(db_session, builder_session_id)
    messages = _rebuild_messages(session_row.conversation or [])

    system = AGENT_BUILDER_SYSTEM_PROMPT
    if session_row.target_id is not None and not messages:
        opener = await build_edit_session_opener(session_row.target_id, db_session=db_session)
        if opener:
            system = (
                AGENT_BUILDER_SYSTEM_PROMPT + "\n\n## Current edit-session context\n\n" + opener
            )

    tools = build_tool_registry(db_session=db_session, builder_session_id=builder_session_id)
    # NOTE (CC19): build_tool_registry / tools.specs() are still used for
    # non-claude-code adapters (anthropic, gemini, openai, openrouter) that
    # handle tool_use turns in-process. For ClaudeCodeAdapter, tool specs are
    # passed through to _complete_with_tools but the in-process tool impls
    # (build_tool_registry implementations) are dead code on that path — the
    # tools execute inside the claude-code subprocess via the MCP server.
    tool_specs = tools.specs()
    messages.append(make_user_message(user_text))

    turn_id = f"{builder_session_id}-{int(time.time())}"
    yield BuilderEvent(
        type="turn_start",
        payload={
            "turn_id": turn_id,
            "session_id": builder_session_id,
            "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )

    conversation = list(messages)
    assistant_text = ""
    stop_reason = "end_turn"

    # CC19: detect ClaudeCodeAdapter so we can set the contextvar and
    # short-circuit the 5-iteration tool_use loop (claude-code's internal
    # agent loop runs all tool-use iterations inside the subprocess).
    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    _is_claude_code_with_tools = isinstance(adapter, ClaudeCodeAdapter) and bool(tool_specs)

    try:
        for _iteration in range(5):
            request = CompletionRequest(
                messages=conversation,
                system=system,
                tools=tool_specs,
                max_tokens=2048,
                cache_system=True,
                cache_tools=True,
            )

            # CC19: set builder_session_id contextvar before calling adapter.complete()
            # so ClaudeCodeAdapter._complete_with_tools can pass it to the MCP server.
            if _is_claude_code_with_tools:
                from artemis.builder.context import builder_session_id_var

                _token = builder_session_id_var.set(builder_session_id)
                try:
                    response = await adapter.complete(request)
                finally:
                    builder_session_id_var.reset(_token)
            else:
                response = await adapter.complete(request)

            conversation.append(response.message)

            # Emit assistant text tokens (chunk-granularity since AnthropicAdapter
            # is non-streaming; yield the whole text as one token event).
            for block in response.message.content:
                if isinstance(block, TextBlock) and block.text:
                    assistant_text += block.text
                    yield BuilderEvent(type="assistant_token", payload={"delta": block.text})

            # CC19 short-circuit: ClaudeCodeAdapter's internal agent loop already
            # completed all tool-use iterations inside the subprocess. The returned
            # response is always end_turn (never tool_use). Break after one
            # iteration to avoid unnecessary retries.
            if _is_claude_code_with_tools:
                stop_reason = response.stop_reason or "end_turn"
                break

            if response.stop_reason != "tool_use":
                stop_reason = response.stop_reason or "end_turn"
                break

            tool_uses = [b for b in response.message.content if isinstance(b, ToolUseBlock)]
            if not tool_uses:
                stop_reason = "end_turn"
                break

            result_blocks: list[ToolResultBlock] = []
            for use in tool_uses:
                yield BuilderEvent(
                    type="tool_call",
                    payload={"tool_call_id": use.id, "tool_name": use.name, "inputs": use.input},
                )
                t0 = time.monotonic()
                is_error = False
                try:
                    entry = tools.get(use.name)
                    if entry is None:
                        raise KeyError(f"tool {use.name!r} not registered")
                    content = await entry.impl(use.input)
                    # Detect proposal_staged: _propose returns {"proposal_id": ..., "status": "pending"}
                    if use.name == "propose":
                        try:
                            parsed = json.loads(content)
                            if "proposal_id" in parsed:
                                yield BuilderEvent(
                                    type="proposal_staged",
                                    payload={
                                        "proposal_id": parsed["proposal_id"],
                                        "kind": use.input.get("kind", ""),
                                        "definition_diff": use.input.get("definition", {}),
                                    },
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass
                except Exception as exc:
                    logger.exception("tool %s raised during stream", use.name)
                    content = f"{type(exc).__name__}: {exc}"
                    is_error = True

                elapsed_ms = int((time.monotonic() - t0) * 1000)
                yield BuilderEvent(
                    type="tool_result",
                    payload={
                        "tool_call_id": use.id,
                        "tool_name": use.name,
                        "ok": not is_error,
                        "result_preview": (content or "")[:200],
                        "duration_ms": elapsed_ms,
                    },
                )
                result_blocks.append(
                    ToolResultBlock(tool_use_id=use.id, content=content, is_error=is_error)
                )

            from artemis.agent.types import Message as AgentMessage

            conversation.append(AgentMessage(role="user", content=list(result_blocks)))

        else:
            stop_reason = "max_iterations"

    except asyncio.CancelledError:
        logger.info(
            "builder stream cancelled (client disconnect) for session %s — stopping LLM call",
            builder_session_id,
        )
        # Persist whatever completed so far before re-raising
        if assistant_text:
            await append_builder_message(db_session, builder_session_id, "user", user_text)
            await append_builder_message(
                db_session, builder_session_id, "assistant", assistant_text
            )
            await db_session.commit()
        raise
    except Exception as exc:
        logger.exception("builder stream error for session %s", builder_session_id)
        yield BuilderEvent(type="error", payload={"code": "stream_error", "message": str(exc)})
        return

    # Persist complete turns
    await append_builder_message(db_session, builder_session_id, "user", user_text)
    await append_builder_message(db_session, builder_session_id, "assistant", assistant_text)
    await db_session.commit()

    yield BuilderEvent(
        type="turn_complete",
        payload={
            "assistant_text": assistant_text,
            "draft": session_row.draft,
            "stop_reason": stop_reason,
        },
    )


async def handle_turn(
    *,
    builder_session_id: int,
    user_text: str,
    adapter: Any,
    db_session: Any,
) -> dict[str, Any]:
    """Process one user turn — thin wrapper that drains handle_turn_stream.

    Returns:
        {"assistant_text": str, "draft": dict | None, "stop_reason": str}
    """
    final: dict[str, Any] | None = None
    async for ev in handle_turn_stream(
        builder_session_id=builder_session_id,
        user_text=user_text,
        adapter=adapter,
        db_session=db_session,
    ):
        if ev.type == "turn_complete":
            final = ev.payload
    if final is None:
        # Error path: return a safe fallback so callers don't crash
        return {"assistant_text": "", "draft": None, "stop_reason": "error"}
    return final


def _rebuild_messages(conversation: list[dict[str, Any]]) -> list[Message]:
    """Reconstruct a list of Message objects from the stored JSONB conversation array."""
    from typing import cast

    from artemis.agent.types import Role

    messages: list[Message] = []
    for entry in conversation:
        raw_role = entry.get("role", "user")
        role: Role = cast(Role, raw_role if raw_role in ("user", "assistant", "system") else "user")
        content = entry.get("content", "")
        if isinstance(content, str):
            messages.append(Message(role=role, content=[TextBlock(text=content)]))
        elif isinstance(content, list):
            # Already structured content blocks — pass through as text
            messages.append(Message(role=role, content=[TextBlock(text=str(content))]))
    return messages
