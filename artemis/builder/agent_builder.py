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
    "turn_start", "tool_call", "tool_result", "assistant_token",
    "proposal_staged", "heartbeat", "turn_complete", "error",
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
                return json.dumps({
                    "error": (
                        "run_ids validation failed: the following IDs were not returned by "
                        f"read_recent_runs in this session and cannot be cited: {bad_ids}. "
                        "Only reference run IDs from the set read_recent_runs returned."
                    )
                })

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

    impl_map = {
        "read_existing": _read_existing,
        "read_capabilities": _read_capabilities,
        "read_recent_runs": _read_recent_runs,
        "propose": _propose,
        "test_run": _test_run,
    }
    for spec_dict in AGENT_BUILDER_TOOL_SPECS:
        tool = Tool(
            name=spec_dict["name"],
            description=spec_dict["description"],
            input_schema=spec_dict["input_schema"],
        )
        registry.register(tool, impl_map[spec_dict["name"]])

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
    """
    from sqlalchemy import select as sa_select

    from artemis.builder import engine
    from artemis.builders.models import Agent

    result = await db_session.execute(
        sa_select(Agent).where(Agent.id == target_id).limit(1)
    )
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
        intro += (
            " Here's what I noticed:\n"
            + "\n".join(summary_lines)
            + "\n\nI'll propose definition changes based on these patterns."
        )
    else:
        intro += " No trajectory summaries are available yet — I'll need a few runs to spot patterns."
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
            system = AGENT_BUILDER_SYSTEM_PROMPT + "\n\n## Current edit-session context\n\n" + opener

    tools = build_tool_registry(db_session=db_session, builder_session_id=builder_session_id)
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
            response = await adapter.complete(request)
            conversation.append(response.message)

            # Emit assistant text tokens (chunk-granularity since AnthropicAdapter
            # is non-streaming; yield the whole text as one token event).
            for block in response.message.content:
                if isinstance(block, TextBlock) and block.text:
                    assistant_text += block.text
                    yield BuilderEvent(type="assistant_token", payload={"delta": block.text})

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
            await append_builder_message(db_session, builder_session_id, "assistant", assistant_text)
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
        role: Role = cast(
            Role, raw_role if raw_role in ("user", "assistant", "system") else "user"
        )
        content = entry.get("content", "")
        if isinstance(content, str):
            messages.append(Message(role=role, content=[TextBlock(text=content)]))
        elif isinstance(content, list):
            # Already structured content blocks — pass through as text
            messages.append(Message(role=role, content=[TextBlock(text=str(content))]))
    return messages
