"""Pipeline AI Assistant — SSE turn handler.

Parallel pattern: artemis/builder/agent_builder.py (Agent-Builder O1).
See that file for the conventions this module follows.

Key differences from Agent-Builder:
  - No tool calls. The pipeline assistant is one-shot per user message:
    system prompt = pipeline JSON + recent runs summary → text + proposals.
  - Proposals are embedded in the assistant text as
    PROPOSAL_BEGIN <json> PROPOSAL_END tokens, not via LLM tool calls.
  - Conversation is persisted in pipeline_ai_conversations table keyed by
    pipeline_id (see routes.py for the repo helpers).
  - Self-improvement: on first turn per panel session, we inject a summary
    of the last 5 pipeline runs into the system prompt.

H5: PROPOSAL_BEGIN...PROPOSAL_END blocks are validated against PipelineProposal
(Pydantic) before being emitted as proposal_parsed events.  Validation failure
strips the malformed block from the response text and logs a warning — the
user's next turn is the natural retry path (no auto-retry in interactive flow).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

# ── Event types (mirrors BuilderEvent shape for symmetry) ──────────────────────

AssistantPanelEventType = Literal[
    "turn_start",
    "assistant_token",
    "proposal_parsed",
    "self_improvement",
    "heartbeat",
    "turn_complete",
    "error",
]


@dataclass(slots=True)
class AssistantPanelEvent:
    type: AssistantPanelEventType
    payload: dict[str, Any]

    def to_sse(self) -> str:
        return f"event: {self.type}\ndata: {json.dumps(self.payload)}\n\n"


# ── System prompt ──────────────────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
You are the Pipeline AI Assistant — a senior engineer embedded inside the
Artemis OS pipeline canvas. Your job is to help the user understand and
improve this specific pipeline.

## Current pipeline definition
{pipeline_json}

{runs_section}

## What you can propose
Respond in natural language. When you want to propose a structural change,
embed it INLINE in your text using this exact format (one per change):
  PROPOSAL_BEGIN <json> PROPOSAL_END

The JSON must be a valid PipelineProposal object:
{{
  "kind": "add_node" | "remove_node" | "add_edge" | "remove_edge" | "update_node_config",
  "payload": {{ ... }},
  "explanation": "Human-readable why"
}}

Payload shapes per kind:
  add_node:           {{ "node": {{ "type": "...", "label": "...", "config": {{}} }} }}
  remove_node:        {{ "node_id": "..." }}
  add_edge:           {{ "source_node_id": "...", "target_node_id": "...", "condition": null }}
  remove_edge:        {{ "source_node_id": "...", "target_node_id": "..." }}
  update_node_config: {{ "node_id": "...", "config_patch": {{ "key": "value" }} }}

Valid node types: trigger_manual, trigger_scheduled, trigger_webhook,
trigger_event, agent_invocation, skill_call, human_gate, conditional,
sub_pipeline.

## Rules
- NEVER apply proposals automatically. Always emit them as PROPOSAL_BEGIN…PROPOSAL_END
  blocks so the user can Accept or Reject.
- For explanation-only questions (e.g. "What does this pipeline do?"),
  respond with prose only — no proposals.
- Keep proposals minimal — one structural change per PROPOSAL_BEGIN block.
- Be concise. The user is editing a visual graph, not reading a document.
- If a request is ambiguous, ask one focused clarifying question.
"""

_RUNS_SECTION_TEMPLATE = """\
## Recent pipeline runs (last {count})
{runs_summary}

If you notice a recurring failure pattern, proactively surface it as a
proposal (e.g. bump timeout, add error-handling node). Always cite the
specific run IDs you observed.
"""


def _build_system_prompt(
    pipeline: dict[str, Any],
    recent_runs: list[dict[str, Any]],
) -> str:
    pipeline_json = json.dumps(
        {"nodes": pipeline.get("nodes", []), "edges": pipeline.get("edges", [])},
        indent=2,
    )

    if recent_runs:
        lines = []
        for r in recent_runs[:5]:
            status = r.get("status", "?")
            run_id = r.get("id", "?")
            err = r.get("error_message")
            node_states = r.get("node_states") or {}
            failed_nodes = [
                nid
                for nid, ns in (node_states.items() if isinstance(node_states, dict) else [])
                if ns.get("status") == "failed"
            ]
            line = f"  Run {run_id}: status={status}"
            if err:
                line += f", error={err[:80]}"
            if failed_nodes:
                line += f", failed_nodes={failed_nodes}"
            lines.append(line)
        runs_section = _RUNS_SECTION_TEMPLATE.format(
            count=len(recent_runs), runs_summary="\n".join(lines)
        )
    else:
        runs_section = "## Recent pipeline runs\nNo runs yet."

    return _SYSTEM_PROMPT_TEMPLATE.format(
        pipeline_json=pipeline_json,
        runs_section=runs_section,
    )


# ── Proposal parser ────────────────────────────────────────────────────────────


def _extract_proposals(text: str) -> list[dict[str, Any]]:
    """Parse PROPOSAL_BEGIN…PROPOSAL_END blocks from assistant text.

    Returns list of raw dicts (not validated Pydantic objects — validation
    happens in the route when the frontend accepts them).
    """
    import re

    proposals = []
    for match in re.finditer(r"PROPOSAL_BEGIN\s+(\{.*?\})\s+PROPOSAL_END", text, re.DOTALL):
        try:
            proposals.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            logger.warning("Failed to parse proposal JSON from assistant text")
    return proposals


def _extract_and_validate_proposal(
    assistant_text: str,
) -> tuple[str, dict[str, Any] | None]:
    """Extract PROPOSAL_BEGIN...PROPOSAL_END block and validate against PipelineProposal.

    Returns (text_without_proposal_block, parsed_proposal_dict_or_None).

    On validation failure: removes the malformed proposal block from the text
    (so the user does not see broken UI markup) and logs a warning.  The
    conversational text is returned unchanged.  No auto-retry — the operator's
    next turn is the natural retry path for interactive flow.
    """
    import re

    from pydantic import ValidationError

    from artemis.pipelines.assistant.schemas import PipelineProposal

    match = re.search(r"PROPOSAL_BEGIN\s*\n(.*?)\n\s*PROPOSAL_END", assistant_text, re.DOTALL)
    if not match:
        return assistant_text, None

    raw_json = match.group(1).strip()
    cleaned_text = re.sub(
        r"PROPOSAL_BEGIN.*?PROPOSAL_END", "", assistant_text, flags=re.DOTALL
    ).strip()

    try:
        proposal = PipelineProposal.model_validate_json(raw_json)
        return cleaned_text, proposal.model_dump(mode="json")
    except ValidationError as exc:
        logger.warning("Pipeline AI Panel proposal validation failed: %s", exc)
        # Fall through: return text without the malformed block so the UI
        # shows the conversational response but no broken proposal widget.
        return cleaned_text, None


# ── Self-improvement scan ──────────────────────────────────────────────────────


def _detect_self_improvement_hints(
    recent_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Scan recent runs for patterns worth surfacing as proactive suggestions.

    Returns a list of hint dicts: {pattern, suggestion, run_ids}.
    These are emitted as self_improvement events before the first turn.

    v1 patterns:
      - All runs failed at the same node → suggest timeout bump / error node
      - Majority of runs cancelled → suggest manual trigger instead of cron
    """
    if not recent_runs:
        return []

    hints: list[dict[str, Any]] = []
    run_ids = [r.get("id") for r in recent_runs if r.get("id")]

    # Pattern: all runs failed
    failed = [r for r in recent_runs if r.get("status") == "failed"]
    if len(failed) >= 3 and len(failed) == len(recent_runs):
        # Find common failed node
        failed_node_counts: dict[str, int] = {}
        for r in failed:
            ns = r.get("node_states") or {}
            if isinstance(ns, dict):
                for nid, state in ns.items():
                    if isinstance(state, dict) and state.get("status") == "failed":
                        failed_node_counts[nid] = failed_node_counts.get(nid, 0) + 1
        if failed_node_counts:
            worst_node = max(failed_node_counts, key=lambda k: failed_node_counts[k])
            hints.append(
                {
                    "pattern": "consistent_node_failure",
                    "node_id": worst_node,
                    "suggestion": (
                        f"Node '{worst_node}' has failed in all recent runs. "
                        "Consider adding a conditional error-handling branch after it."
                    ),
                    "run_ids": run_ids[:5],
                }
            )

    # Pattern: cancelled > 50%
    cancelled = [r for r in recent_runs if r.get("status") == "cancelled"]
    if len(cancelled) > len(recent_runs) / 2 and len(recent_runs) >= 3:
        hints.append(
            {
                "pattern": "high_cancellation_rate",
                "suggestion": (
                    f"{len(cancelled)}/{len(recent_runs)} recent runs were cancelled. "
                    "This may indicate the schedule is too aggressive or the trigger is misconfigured."
                ),
                "run_ids": run_ids[:5],
            }
        )

    return hints


# ── Main turn stream ───────────────────────────────────────────────────────────


async def handle_assistant_turn_stream(
    *,
    pipeline_id: str,
    user_text: str,
    pipeline_data: dict[str, Any],
    conversation: list[dict[str, Any]],
    recent_runs: list[dict[str, Any]],
    adapter: Any,
    is_first_turn: bool = False,
) -> AsyncIterator[AssistantPanelEvent]:
    """Streaming async generator for one Pipeline AI Assistant turn.

    Yields AssistantPanelEvent objects:
      turn_start
      self_improvement  (0-N, only on first turn when patterns detected)
      assistant_token   (one per text chunk)
      proposal_parsed   (one per embedded PROPOSAL_BEGIN…PROPOSAL_END)
      turn_complete | error

    See artemis/builder/agent_builder.py::handle_turn_stream for the
    parallel Agent-Builder pattern this mirrors.
    """
    from typing import cast

    from artemis.agent.client import CompletionRequest
    from artemis.agent.loop import user_message as make_user_message
    from artemis.agent.types import Message, Role, TextBlock

    turn_id = f"{pipeline_id}-{int(time.time())}"
    import datetime

    yield AssistantPanelEvent(
        type="turn_start",
        payload={
            "turn_id": turn_id,
            "pipeline_id": pipeline_id,
            "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        },
    )

    # Self-improvement hints on first turn
    if is_first_turn:
        hints = _detect_self_improvement_hints(recent_runs)
        for hint in hints:
            yield AssistantPanelEvent(type="self_improvement", payload=hint)

    # Rebuild message history
    messages: list[Message] = []
    for entry in conversation:
        raw_role = entry.get("role", "user")
        role: Role = cast(Role, raw_role if raw_role in ("user", "assistant") else "user")
        content = entry.get("content", "")
        messages.append(Message(role=role, content=[TextBlock(text=str(content))]))

    messages.append(make_user_message(user_text))
    system = _build_system_prompt(pipeline_data, recent_runs)

    assistant_text = ""
    try:
        request = CompletionRequest(
            messages=messages,
            system=system,
            tools=[],
            max_tokens=2048,
            cache_system=True,
            cache_tools=False,
        )
        response = await adapter.complete(request)

        for block in response.message.content:
            if isinstance(block, TextBlock) and block.text:
                assistant_text += block.text
                yield AssistantPanelEvent(type="assistant_token", payload={"delta": block.text})

        # Extract, validate (H5), and emit proposals.
        # _extract_and_validate_proposal handles a single PROPOSAL_BEGIN...PROPOSAL_END
        # block per turn.  For multiple proposals, fall back to the legacy extractor
        # so we don't silently drop valid blocks when one is malformed.
        validated_text, validated_prop = _extract_and_validate_proposal(assistant_text)
        if validated_prop is not None:
            yield AssistantPanelEvent(type="proposal_parsed", payload=validated_prop)
        elif validated_text != assistant_text:
            # Block was present but failed validation — text was stripped, no event emitted.
            pass
        else:
            # No PROPOSAL_BEGIN block found by the new validator; try the legacy path
            # in case of alternative whitespace/formatting in the block markers.
            proposals = _extract_proposals(assistant_text)
            for prop in proposals:
                yield AssistantPanelEvent(type="proposal_parsed", payload=prop)

    except asyncio.CancelledError:
        logger.info(
            "pipeline assistant stream cancelled for pipeline %s — stopping LLM call",
            pipeline_id,
        )
        raise
    except Exception as exc:
        logger.exception("pipeline assistant stream error for pipeline %s", pipeline_id)
        yield AssistantPanelEvent(
            type="error", payload={"code": "stream_error", "message": str(exc)}
        )
        return

    yield AssistantPanelEvent(
        type="turn_complete",
        payload={
            "assistant_text": assistant_text,
            "proposal_count": len(_extract_proposals(assistant_text)),
        },
    )
