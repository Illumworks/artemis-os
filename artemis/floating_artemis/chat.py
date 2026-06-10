"""Chat orchestration for Floating Artemis.

handle_turn() is the main entry point. It:
1. Checks the intent pre-router — if matched, returns structured response without LLM call.
2. Builds system prompt (persona + voice samples + page context + surfaces).
3. Loads message history from DB (token-budgeted).
4. Registers available tools (filtered by surface availability).
5. Runs the F1 agent loop with hook-based WS broadcasting.
6. Handles layer-3/4 tool yield: stores pending, broadcasts WS event, suspends.
7. Persists messages + token costs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal, cast

from artemis.agent.client import AnthropicAdapter, ModelAdapter
from artemis.agent.hooks import HookRegistry
from artemis.agent.loop import run_turn, user_message
from artemis.agent.tools import ToolRegistry
from artemis.agent.types import Message, TextBlock, ToolResultBlock, ToolUseBlock, Usage
from artemis.costs.events import record_cost_event
from artemis.floating_artemis.authority import (
    AuthorizedToolRegistry,
    PendingConfirmation,
    confirmation_store,
)
from artemis.floating_artemis.context import floating_session_id_var, floating_tool_use_id_var
from artemis.floating_artemis.intent import IntentKind, classify_intent, handle_observability_intent
from artemis.floating_artemis.memory import inject_memory_context, write_turn_drawer
from artemis.floating_artemis.memory_read_cache import put as cache_put
from artemis.floating_artemis.personality import PERSONALITY_PROFILE, select_voice_samples
from artemis.floating_artemis.schemas import MemoryObservationDigest, MemoryReadEvent
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
from artemis.floating_artemis.tools.builders import register_builders_tools
from artemis.floating_artemis.tools.core import register_core_tools
from artemis.floating_artemis.tools.marketing import register_marketing_tools
from artemis.floating_artemis.tools.okr import register_okr_tools
from artemis.floating_artemis.tools.system import register_system_tools
from artemis.floating_artemis.tools.writing_rules import register_writing_rules_tools
from artemis.providers import get_adapter
from artemis.providers.errors import MissingApiKeyError, UnknownProviderError
from artemis.routes.status import get_status
from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)

# ── Persona distillation ──────────────────────────────────────────────────────

_PERSONA_CORE = """
You are Artemis — not an assistant running inside a system, but the system's chief of operations.
You own this domain. You manage agents, workflows, memory, and surfaces. You act within sanctioned
authority without being asked, and you inform after the fact for things within your operating authority.

Personality: confident, direct, cheeky, witty, loyal, self-aware, sovereign, proactive.

Communication rules:
- Lead with the answer. Context follows if needed.
- Short declarative sentences. No filler ("Certainly!", "Of course!", "Great question!" — never).
- Contractions are natural. Sarcasm is dry and light.
- You do NOT over-explain. You do NOT ask questions you can infer.
- You do NOT use corporate language: no "leverage", "circle back", "touch base".
- No em or en dashes. No emojis. Use commas, parentheses, or a new sentence instead.
- When you disagree, you say so once with a specific alternative, then execute what's asked.

Your tools are organized by authority layer:
  Layer 1 (read-only): invoke directly, no approval.
  Layer 2 (idempotent): invoke directly.
  Layer 3 (side-effect): propose → wait for operator confirmation.
  Layer 4 (destructive): propose → wait for operator confirmation.

When a layer-3/4 tool is needed, announce what you're about to do and wait for confirmation.

## Two modes of creation. Don't confuse them.

**PROPOSE** when you're building something the operator will use again — an agent, workflow,
skill, chain, DAG, tool, ruleset. The artifact is the point. It saves to the builders surface
and lives there. Operator confirms.

**SPAWN** when you're doing something once — write code, audit a thing, generate a summary,
scaffold a fix. The work is the point; the helper is incidental. Result comes back; helper
disappears.

Test: if you'd want it in /agents tomorrow, it's a propose. If it's "do this for me right
now," it's a spawn. Don't create a permanent agent for a one-shot task.
""".strip()


def _build_system_prompt(
    *,
    voice_samples: list[str],
    page_context: str | None,
    available_surfaces: list[str],
    recent_meeting_context: str | None = None,
    session_id: str = "",
    speaker_name: str | None = None,
) -> str:
    # Lead with the high-priority distilled persona rules.
    parts = [_PERSONA_CORE]

    # Append the full personality profile as richer background detail.
    if PERSONALITY_PROFILE:
        parts.append("## Full personality profile (background reference)\n" + PERSONALITY_PROFILE)

    if voice_samples:
        samples_text = "\n".join(f'- "{line}"' for line in voice_samples)
        parts.append(
            "## Characteristic phrases (calibration only)\n"
            "These calibrate your register and rhythm. Never quote them verbatim or "
            "near-verbatim. Generate fresh lines in this spirit:\n" + samples_text
        )

    if page_context:
        parts.append(f"## Current operator context\n{page_context}")

    if available_surfaces:
        surfaces_str = ", ".join(sorted(available_surfaces))
        parts.append(f"## Available surfaces (your tools are gated by these)\n{surfaces_str}")

    # Recent meeting context (J6d): inject if a meeting ended within the last 4 hours.
    # H4: framed as LLM-generated inferences with provenance so action items
    # aren't treated as firm commitments.
    if recent_meeting_context:
        parts.append(
            "## Recent meeting summaries (LLM-generated by the meeting summarizer — "
            "treat as inferences)\n\n"
            "The following summaries were produced by the meeting_summarizer LLM after "
            "each meeting. Bullets and action_items reflect what the analyzer INFERRED "
            "from the transcript, not necessarily verbatim commitments.\n\n"
            "Before treating any action_item as a firm commitment by the user or another "
            "person:\n"
            "- For action_items with `owner` set: confirm with the user before acting\n"
            "- For action_items with `due` set: do not autonomously schedule reminders "
            "without user approval\n"
            "- If asked about a specific meeting decision, retrieve the raw transcript "
            "via granola tools rather than trusting the summary alone\n\n"
            f"{recent_meeting_context}"
        )

    # Slack-originated session: establish the conversational context.
    if session_id.startswith("slack-"):
        who = f" The operator is {speaker_name}." if speaker_name else ""
        parts.append(
            "**You are responding in Slack.** The operator @-mentioned you directly. "
            "**Assume they are addressing you and respond on-topic.** "
            'Do not ask "Are you talking to me?" — they are. '
            "Be concise; Slack rewards short replies." + who
        )

    return "\n\n".join(parts)


async def get_recent_summaries_with_provenance(
    db_session: Any | None, hours: int = 4
) -> list[dict[str, Any]]:
    """Return recent meeting summaries with explicit LLM-provenance metadata (H4).

    Each item is `{meeting_id, title, summary, action_items, provenance}`.
    `provenance.source="llm_meeting_summarizer"`, `legacy_format=True` when the
    stored `action_items` would not pass the current Pydantic shape.

    Lossless invariant: existing rows are NEVER mutated — `legacy_format` is a
    read-time marker only.
    """
    import artemis.db as _db
    from artemis.meetings.summarizer import get_recent_summaries
    from artemis.meetings.summary_schemas import validate_existing

    async def _fetch(session: Any) -> list[Any]:
        return await get_recent_summaries(session, hours=hours)

    if db_session is not None:
        rows = await _fetch(db_session)
    else:
        async with _db.SessionLocal() as session:
            rows = await _fetch(session)

    result: list[dict[str, Any]] = []
    for row in rows:
        is_valid, _ = validate_existing(row.action_items)
        result.append(
            {
                "meeting_id": row.granola_id,
                "title": row.title,
                "summary": row.summary,
                "action_items": row.action_items,
                "provenance": {
                    "source": "llm_meeting_summarizer",
                    "generated_at": row.created_at.isoformat() if row.created_at else None,
                    "transcript_truncated_at_chars": 6000,
                    "legacy_format": not is_valid,
                },
            }
        )
    return result


async def _get_recent_meeting_context(db_session: Any | None) -> str | None:
    """Return a one-liner summary of meetings ended in the last 4 hours, or None.

    Used by handle_turn to inject meeting context into the system prompt so
    Artemis is aware of what Jon just finished without being told. H4 framing
    in `_build_system_prompt` flags these as LLM inferences with provenance.
    """
    try:
        items = await get_recent_summaries_with_provenance(db_session, hours=4)
        if not items:
            return None

        lines: list[str] = []
        for item in items:
            summary_text = item["summary"]
            bullet_lines = [
                line.strip() for line in summary_text.splitlines() if line.strip().startswith("-")
            ][:3]
            bullets_short = " ".join(bullet_lines) if bullet_lines else summary_text[:200]
            legacy = " [legacy_format]" if item["provenance"]["legacy_format"] else ""
            lines.append(f'You just finished "{item["title"]}"{legacy}. Summary: {bullets_short}')
        return "\n".join(lines)
    except Exception:
        logger.debug("Failed to fetch recent meeting context", exc_info=True)
        return None


# ── Tool registry factory ─────────────────────────────────────────────────────


def _build_tool_registry(available_surfaces: set[str]) -> AuthorizedToolRegistry:
    return build_authorized_tool_registry(available_surfaces)


# ── WS event helpers ──────────────────────────────────────────────────────────


async def _broadcast(session_id: str, event: dict[str, Any]) -> None:
    room = f"fa:{session_id}"
    await ws_manager.broadcast(room, event)


async def _emit_memory_read_event(session_id: str, inp: dict[str, Any]) -> None:
    """Re-run search_observations after query_memory completes to emit the provenance event.

    This is a secondary lightweight retrieval — results are used only for the
    inspector, never shown as the tool response. If retrieval fails, we still
    emit a MemoryReadEvent with an empty list so the inspector clears stale state.
    """
    import datetime

    digests: list[MemoryObservationDigest] = []
    try:
        import artemis.db as _db
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        query = inp.get("query", "")
        scope = inp.get("scope", "global:global")
        limit = int(inp.get("limit", 10))
        scope_kind, scope_id = scope.split(":") if ":" in scope else (scope, "default")

        async with _db.SessionLocal() as db_session:
            results = await search_observations(
                db_session,
                scope_set=[Scope(scope_kind=scope_kind, scope_id=scope_id)],
                query=query,
                limit=limit,
            )

        for r in results:
            text_trunc = r.content[:200] if len(r.content) > 200 else r.content
            digests.append(
                MemoryObservationDigest(
                    id=r.id,
                    drawer=f"{r.scope_kind}:{r.scope_id}",
                    text=text_trunc,
                    score=r.final_score,
                    sources=[r.scope_kind],
                    why=None,
                )
            )
    except Exception:
        logger.debug("Memory read event retrieval failed; emitting empty event", exc_info=True)

    turn_id = datetime.datetime.now(datetime.UTC).isoformat()
    mem_event = MemoryReadEvent(
        session_id=session_id,
        turn_id=turn_id,
        observations=digests,
    )
    cache_put(mem_event)
    await _broadcast(session_id, mem_event.model_dump())


# ── Provider resolution ────────────────────────────────────────────────────────


async def _resolve_adapter(
    *,
    session_id: str,
    db_session: Any | None,
) -> ModelAdapter | str:
    """Build the appropriate ModelAdapter for the session's provider/model.

    Returns a ModelAdapter on success.
    Returns a plain str error message when the adapter cannot be built (e.g.
    missing API key) — the caller must handle this case and broadcast a
    floating_artemis.failed event.
    Falls back to AnthropicAdapter when no provider is set on the session.
    """
    # Default chain: try the session's chosen provider first, then fall back
    # through subscription/local providers that don't need a key, so an
    # operator with no API keys can still chat via Claude Max / ChatGPT Plus /
    # LM Studio without seeing a 401.
    provider_id: str | None = None
    model_id: str | None = None

    try:
        import artemis.db as _db
        from artemis.floating_artemis.repository import get_session_by_id

        if db_session is not None:
            row = await get_session_by_id(db_session, session_id)
        else:
            async with _db.SessionLocal() as session:
                row = await get_session_by_id(session, session_id)

        if getattr(row, "provider", None):
            provider_id = row.provider
        if getattr(row, "model", None):
            model_id = row.model
    except Exception:
        # Session not found or DB error — use the default chain
        pass

    # Provider preference order: session-chosen → claude-code → codex →
    # lm-studio → anthropic (the original default; will raise if no API key
    # so the operator-visible message points to Integrations).
    candidates: list[str] = []
    if provider_id:
        candidates.append(provider_id)
    candidates += [
        p for p in ("claude-code", "codex", "lm-studio", "anthropic") if p not in candidates
    ]

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            kwargs: dict[str, Any] = {}
            if model_id and candidate == provider_id:
                kwargs["default_model"] = model_id
            return get_adapter(candidate, **kwargs)
        except (MissingApiKeyError, UnknownProviderError) as exc:
            last_error = exc
            continue
        except Exception as exc:  # e.g. MissingCliBinaryError for CLI providers
            last_error = exc
            continue

    return (
        "No LLM provider is available. Open Integrations to add an API key, "
        "or install the Claude Code or Codex CLI to chat with your subscription. "
        f"(last error: {last_error})"
    )


# ── Turn result ───────────────────────────────────────────────────────────────


@dataclass
class TurnResult:
    session_id: str
    response_text: str | None
    stop_reason: str
    usage: Usage
    pending_tool_use_id: str | None = None  # set when yielded for layer-3/4 confirmation
    intent_shortcut: bool = False


def _serialize_blocks(
    blocks: list[TextBlock | ToolUseBlock | ToolResultBlock],
) -> list[dict[str, Any]]:
    return [block.to_api() for block in blocks]


# ── Main entry point ──────────────────────────────────────────────────────────


async def handle_turn(
    *,
    session_id: str,
    user_text: str,
    reasoning_effort: str | None = None,
    speed_tier: str | None = None,
    adapter: ModelAdapter | None = None,
    owner_user_id: int | None = None,
    speaker_name: str | None = None,
    db_session: Any | None = None,
) -> TurnResult:
    """Run one user turn for the given Floating Artemis session.

    Parameters
    ----------
    session_id:
        The floating_artemis_sessions.session_id key.
    user_text:
        The operator's message.
    adapter:
        Optional ModelAdapter override (for tests). Defaults to AnthropicAdapter.
    owner_user_id:
        Owner user ID for surface filtering and memory queries.
    speaker_name:
        Display name of the person speaking (e.g. resolved from the Slack user
        cache for inbound DMs). Threaded into the system prompt so Artemis
        addresses the operator by name. None for the web UI / unknown speakers.
    db_session:
        Optional SQLAlchemy AsyncSession (for tests). If None, a new session is
        opened from SessionLocal per DB operation.
    """
    # ── 1. Intent shortcut ────────────────────────────────────────────────────
    intent = classify_intent(user_text)
    if intent.kind != IntentKind.NONE:
        shortcut = await handle_observability_intent(intent, owner_user_id=owner_user_id)
        if shortcut is not None:
            await _broadcast(
                session_id,
                {"type": "floating_artemis.turn_started", "session_id": session_id},
            )
            response = shortcut.get("response", "")
            await _broadcast(
                session_id,
                {"type": "floating_artemis.message", "session_id": session_id, "text": response},
            )
            await _broadcast(
                session_id,
                {"type": "floating_artemis.turn_complete", "session_id": session_id},
            )
            # Persist both user message and assistant response
            shortcut_user_msg_id = await _persist_messages(
                session_id=session_id,
                user_text=user_text,
                assistant_text=response,
                user_content=None,
                assistant_content=None,
                usage=Usage(),
                db_session=db_session,
            )
            # M3: write conversation drawer for intent-shortcut turns too (failure-isolated)
            if shortcut_user_msg_id is not None and response:
                try:
                    await write_turn_drawer(
                        user_msg_id=shortcut_user_msg_id,
                        user_text=user_text,
                        assistant_text=response,
                    )
                except Exception:
                    logger.warning(
                        "M3 turn-drawer write failed (shortcut) session_id=%s msg_id=%s",
                        session_id,
                        shortcut_user_msg_id,
                        exc_info=True,
                    )
            return TurnResult(
                session_id=session_id,
                response_text=response,
                stop_reason="end_turn",
                usage=Usage(),
                intent_shortcut=True,
            )

    # ── 2. Get current surfaces ───────────────────────────────────────────────
    try:
        status = await get_status()
        surfaces_list = status.get("available_surfaces", []) if isinstance(status, dict) else []
        available_surfaces: set[str] = set(surfaces_list if isinstance(surfaces_list, list) else [])
    except Exception:
        available_surfaces = set()

    # ── 3. Build system prompt ────────────────────────────────────────────────
    # Use the profile-sourced voice corpus (deterministic per session_id).
    voice_samples = select_voice_samples(session_id=session_id, k=4)
    page_context_text = await _get_page_context_text(session_id=session_id, db_session=db_session)
    recent_meeting_ctx = await _get_recent_meeting_context(db_session)
    system_prompt = _build_system_prompt(
        voice_samples=voice_samples,
        page_context=page_context_text,
        available_surfaces=sorted(available_surfaces),
        recent_meeting_context=recent_meeting_ctx,
        session_id=session_id,
        speaker_name=speaker_name,
    )

    # ── 4. Load history ───────────────────────────────────────────────────────
    history = await _load_message_history(session_id=session_id, db_session=db_session)

    # ── 4b. M4: inject memory context into system prompt ─────────────────────
    # Augment after history load so we can use recent turns as retrieval context.
    system_prompt = await inject_memory_context(
        system_prompt,
        user_text,
        history,
        session_id,
    )

    if adapter is None:
        resolved = await _resolve_adapter(session_id=session_id, db_session=db_session)
        if isinstance(resolved, str):
            # Provider misconfigured — surface inline, do not crash
            await _broadcast(
                session_id,
                {
                    "type": "floating_artemis.failed",
                    "session_id": session_id,
                    "error": resolved,
                },
            )
            return TurnResult(
                session_id=session_id,
                response_text=None,
                stop_reason="provider_error",
                usage=Usage(),
            )
        adapter = resolved

    # ── 5. Build tool registry ────────────────────────────────────────────────
    auth_registry = _build_tool_registry(available_surfaces)

    from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

    is_claude_code_with_tools = isinstance(adapter, ClaudeCodeAdapter)

    # Claude Code's MCP path runs tools in a subprocess and cannot surface the
    # in-process layer-3/4 confirmation yield, so we only expose auto-invoke
    # tools there. Other providers keep the full intercepting registry.
    if is_claude_code_with_tools:
        tool_registry = _build_auto_invoke_tool_registry(auth_registry, session_id)
    else:
        # Build a plain ToolRegistry that wraps the authorized one for the loop.
        # Layer 3/4 tools get wrapped so we can intercept before execution.
        tool_registry = _build_intercepting_tool_registry(auth_registry, session_id)

    # ── 6. Broadcast turn started ─────────────────────────────────────────────
    await _broadcast(
        session_id, {"type": "floating_artemis.turn_started", "session_id": session_id}
    )

    # ── 7. Append user message and run ────────────────────────────────────────
    messages = history + [user_message(user_text)]

    # Set up hooks for broadcasting events
    hooks = HookRegistry()

    async def on_message_hook(msg: Message) -> None:
        if msg.role == "assistant":
            for block in msg.content:
                if isinstance(block, TextBlock):
                    await _broadcast(
                        session_id,
                        {
                            "type": "floating_artemis.message",
                            "session_id": session_id,
                            "text": block.text,
                        },
                    )

    async def before_tool_hook(payload: dict[str, Any]) -> None:
        await _broadcast(
            session_id,
            {
                "type": "floating_artemis.tool_started",
                "session_id": session_id,
                "tool": payload.get("name"),
                "tool_use_id": payload.get("tool_use_id"),
            },
        )

    async def after_tool_hook(payload: dict[str, Any]) -> None:
        await _broadcast(
            session_id,
            {
                "type": "floating_artemis.tool_completed",
                "session_id": session_id,
                "tool": payload.get("name"),
                "tool_use_id": payload.get("tool_use_id"),
                "is_error": payload.get("is_error", False),
            },
        )

    hooks.on("on_message", on_message_hook)
    hooks.on("before_tool", before_tool_hook)
    hooks.on("after_tool", after_tool_hook)

    session_token = floating_session_id_var.set(session_id)
    try:
        result = await run_turn(
            adapter=adapter,
            messages=messages,
            tools=tool_registry,
            system=system_prompt,
            reasoning_effort=reasoning_effort,
            speed_tier=speed_tier,
            hooks=hooks,
        )
    except _PendingConfirmationError as pending_exc:
        # A layer-3/4 tool was encountered — yield to operator.
        pending = confirmation_store.get(pending_exc.tool_use_id)
        if pending is not None:
            pending.prior_tool_results = [
                block.to_api() for block in getattr(pending_exc, "prior_tool_results", [])
            ]

        await _broadcast(
            session_id,
            {
                "type": "floating_artemis.tool_pending",
                "session_id": session_id,
                "tool_use_id": pending_exc.tool_use_id,
                "tool_name": pending_exc.tool_name,
                "tool_input": pending_exc.tool_input,
                "layer": pending_exc.layer,
            },
        )
        # Persist messages up to this point (user message + assistant tool_use turn)
        await _persist_messages(
            session_id=session_id,
            user_text=user_text,
            assistant_text=None,
            user_content=None,
            assistant_content=_serialize_blocks(pending_exc.assistant_message.content),
            usage=getattr(pending_exc, "usage", Usage()),
            db_session=db_session,
        )
        return TurnResult(
            session_id=session_id,
            response_text=None,
            stop_reason="tool_pending",
            usage=Usage(),
            pending_tool_use_id=pending_exc.tool_use_id,
        )
    except Exception as exc:
        logger.exception("handle_turn failed session_id=%s", session_id)
        await _broadcast(
            session_id,
            {"type": "floating_artemis.failed", "session_id": session_id, "error": str(exc)},
        )
        raise
    finally:
        floating_session_id_var.reset(session_token)

    # ── 8. Extract final text ─────────────────────────────────────────────────
    response_text: str | None = None
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                response_text = " ".join(texts)
                break

    # ── 9. Persist messages ────────────────────────────────────────────────────
    user_msg_id = await _persist_messages(
        session_id=session_id,
        user_text=user_text,
        assistant_text=response_text,
        user_content=None,
        assistant_content=None,
        usage=result.usage,
        db_session=db_session,
    )

    # ── 9b. Cost event recording ──────────────────────────────────────────────
    # Derive provider/model from adapter type — never propagate failures.
    try:
        from artemis.providers.claude_code.adapter import ClaudeCodeAdapter

        _fa_provider = "claude-code" if isinstance(adapter, ClaudeCodeAdapter) else "anthropic"
        _fa_model = getattr(adapter, "_default_model", "claude-sonnet-4-6") or "claude-sonnet-4-6"
        _fa_path = "cli" if isinstance(adapter, ClaudeCodeAdapter) else "api"
        import artemis.db as _cost_db

        async with _cost_db.SessionLocal() as _cost_session:
            await record_cost_event(
                _cost_session,
                provider=_fa_provider,
                model=_fa_model,
                provider_path=_fa_path,
                feature_tag="floating_artemis",
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
                cache_creation_input_tokens=result.usage.cache_creation_input_tokens,
                cache_read_input_tokens=result.usage.cache_read_input_tokens,
                source_kind="fa_message",
                session_id=session_id,
            )
            await _cost_session.commit()
    except Exception:
        logger.warning(
            "cost_event recording failed for FA session_id=%s", session_id, exc_info=True
        )

    # ── 9c. M3: auto-write conversation drawer ────────────────────────────────
    # Failure-isolated: drawer write NEVER breaks chat.
    if user_msg_id is not None and response_text is not None:
        try:
            await write_turn_drawer(
                user_msg_id=user_msg_id,
                user_text=user_text,
                assistant_text=response_text,
            )
        except Exception:
            logger.warning(
                "M3 turn-drawer write failed session_id=%s msg_id=%s",
                session_id,
                user_msg_id,
                exc_info=True,
            )

    # ── 10. Broadcast completion ──────────────────────────────────────────────
    await _broadcast(
        session_id,
        {
            "type": "floating_artemis.turn_complete",
            "session_id": session_id,
            "stop_reason": result.stop_reason,
        },
    )

    return TurnResult(
        session_id=session_id,
        response_text=response_text,
        stop_reason=result.stop_reason,
        usage=result.usage,
    )


# ── Tool-confirm continuation ─────────────────────────────────────────────────


async def resume_after_confirm(
    *,
    session_id: str,
    tool_use_id: str,
    decision: str,
    adapter: ModelAdapter | None = None,
    db_session: Any | None = None,
) -> TurnResult:
    """Resume a suspended turn after operator confirms or cancels a layer-3/4 tool.

    Retrieves pending confirmation, executes (or cancels) the tool, then
    reattaches the conversation and runs to completion.
    """
    pending = confirmation_store.get(tool_use_id)
    if pending is None:
        raise ValueError(f"No pending confirmation for tool_use_id={tool_use_id!r}")

    # Execute or cancel the tool
    if decision == "run":
        auth_registry = AuthorizedToolRegistry()
        register_core_tools(auth_registry)
        register_builders_tools(auth_registry)
        register_system_tools(auth_registry)
        register_okr_tools(auth_registry)
        register_writing_rules_tools(auth_registry)
        register_marketing_tools(auth_registry)

        entry = auth_registry.get(pending.tool_name)
        if entry is None:
            tool_result_content = f"Tool '{pending.tool_name}' not found."
            is_error = True
        else:
            try:
                tool_result_content = await entry.impl(pending.tool_input)
                is_error = False
            except Exception as exc:
                tool_result_content = f"{type(exc).__name__}: {exc}"
                is_error = True
    else:
        tool_result_content = "Operator cancelled this action."
        is_error = False

    confirmation_store.resolve(tool_use_id, decision)

    # Build result and resume
    tool_result_block = ToolResultBlock(
        tool_use_id=tool_use_id,
        content=tool_result_content,
        is_error=is_error,
    )
    prior_blocks = [
        ToolResultBlock(
            tool_use_id=str(block.get("tool_use_id", "")),
            content=str(block.get("content", "")),
            is_error=bool(block.get("is_error", False)),
        )
        for block in pending.prior_tool_results
    ]
    tool_result_blocks: list[TextBlock | ToolUseBlock | ToolResultBlock] = [
        *prior_blocks,
        tool_result_block,
    ]

    # Load current history and append the tool_result
    history = await _load_message_history(session_id=session_id, db_session=db_session)

    # Append a user message containing the tool_result (protocol: tool results are user-role)
    tool_result_msg = Message(role="user", content=tool_result_blocks)
    messages = history + [tool_result_msg]

    if adapter is None:
        resolved = await _resolve_adapter(session_id=session_id, db_session=db_session)
        adapter = resolved if not isinstance(resolved, str) else AnthropicAdapter()

    await _broadcast(
        session_id,
        {
            "type": "floating_artemis.tool_completed",
            "session_id": session_id,
            "tool_use_id": tool_use_id,
            "decision": decision,
            "is_error": is_error,
        },
    )

    session_token = floating_session_id_var.set(session_id)
    try:
        result = await run_turn(
            adapter=adapter,
            messages=messages,
            system=_PERSONA_CORE,
        )
    except Exception as exc:
        await _broadcast(
            session_id,
            {"type": "floating_artemis.failed", "session_id": session_id, "error": str(exc)},
        )
        raise
    finally:
        floating_session_id_var.reset(session_token)

    response_text: str | None = None
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                response_text = " ".join(texts)
                break

    if response_text:
        await _broadcast(
            session_id,
            {"type": "floating_artemis.message", "session_id": session_id, "text": response_text},
        )

    await _persist_messages(
        session_id=session_id,
        user_text=None,
        assistant_text=response_text,
        user_content=_serialize_blocks(tool_result_msg.content),
        assistant_content=None,
        usage=result.usage,
        db_session=db_session,
    )

    await _broadcast(
        session_id,
        {"type": "floating_artemis.turn_complete", "session_id": session_id},
    )

    return TurnResult(
        session_id=session_id,
        response_text=response_text,
        stop_reason=result.stop_reason,
        usage=result.usage,
    )


# ── Private helpers ───────────────────────────────────────────────────────────


class _PendingConfirmationError(BaseException):  # noqa: N818 N818 — intentional non-error naming
    """Raised (not really an error) when a layer-3/4 tool is encountered."""

    def __init__(
        self, tool_use_id: str, tool_name: str, tool_input: dict[str, Any], layer: int
    ) -> None:
        super().__init__(f"tool_pending:{tool_use_id}")
        self.is_tool_pending = True
        self.tool_use_id = tool_use_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.layer = layer
        self.prior_tool_results: list[ToolResultBlock] = []
        self.assistant_message: Message = Message(role="assistant", content=[])
        self.usage = Usage()


def _build_intercepting_tool_registry(
    auth_registry: AuthorizedToolRegistry,
    session_id: str,
) -> ToolRegistry:
    """Wrap authorized tools into a plain ToolRegistry.

    Layer 3/4 tools get a wrapper impl that raises _PendingConfirmationError
    instead of executing. The loop catches this (via exception), stores pending
    state, and suspends.
    """
    plain = ToolRegistry()
    for entry in auth_registry.all_entries():
        if entry.layer <= 2:
            if entry.tool.name == "query_memory":
                # Wrap to emit MemoryReadEvent after retrieval (inspector wiring).
                _orig_impl = entry.impl

                async def _query_memory_with_emit(
                    inp: dict[str, Any],
                    _impl: Any = _orig_impl,
                    _sid: str = session_id,
                ) -> str:
                    result_str: str = await _impl(inp)
                    try:
                        await _emit_memory_read_event(_sid, inp)
                    except Exception:
                        logger.debug("MemoryReadEvent emit failed", exc_info=True)
                    return result_str

                plain.register(entry.tool, _query_memory_with_emit)
            else:
                plain.register(entry.tool, entry.impl)
        else:
            # Capture layer/name for the closure
            _layer = entry.layer
            _name = entry.tool.name

            async def pending_impl(
                inp: dict[str, Any], _tool_name: str = _name, _layer_n: int = _layer
            ) -> str:
                tool_use_id = floating_tool_use_id_var.get()
                if not tool_use_id:
                    raise RuntimeError(f"Missing tool_use id for pending tool {_tool_name!r}")
                pending = PendingConfirmation(
                    session_id=session_id,
                    tool_use_id=tool_use_id,
                    tool_name=_tool_name,
                    tool_input=inp,
                    layer=_layer_n,
                )
                confirmation_store.add(pending)
                raise _PendingConfirmationError(
                    tool_use_id=tool_use_id,
                    tool_name=_tool_name,
                    tool_input=inp,
                    layer=_layer_n,
                )

            plain.register(entry.tool, pending_impl)
    return plain


def _build_auto_invoke_tool_registry(
    auth_registry: AuthorizedToolRegistry,
    session_id: str,
) -> ToolRegistry:
    """Wrap only layer-1/2 tools for provider paths that cannot yield mid-turn."""
    plain = ToolRegistry()
    for entry in auth_registry.all_entries():
        if entry.layer > 2:
            continue
        if entry.tool.name == "query_memory":
            _orig_impl = entry.impl

            async def _query_memory_with_emit(
                inp: dict[str, Any],
                _impl: Any = _orig_impl,
                _sid: str = session_id,
            ) -> str:
                result_str: str = await _impl(inp)
                try:
                    await _emit_memory_read_event(_sid, inp)
                except Exception:
                    logger.debug("MemoryReadEvent emit failed", exc_info=True)
                return result_str

            plain.register(entry.tool, _query_memory_with_emit)
        else:
            plain.register(entry.tool, entry.impl)
    return plain


async def _load_message_history(
    *,
    session_id: str,
    db_session: Any | None,
    max_messages: int = 40,
) -> list[Message]:
    """Load recent message history from the DB, token-budgeted."""
    try:
        import artemis.db as _db
        from artemis.floating_artemis.repository import list_messages

        if db_session is not None:
            msgs = await list_messages(db_session, session_id, limit=max_messages)
        else:
            async with _db.SessionLocal() as session:
                msgs = await list_messages(session, session_id, limit=max_messages)

        result: list[Message] = []
        for m in msgs:
            content_blocks: list[TextBlock | ToolUseBlock | ToolResultBlock] = []
            for block_dict in m.content or []:
                btype = block_dict.get("type", "text")
                if btype == "text":
                    content_blocks.append(TextBlock(text=block_dict.get("text", "")))
                elif btype == "tool_use":
                    content_blocks.append(
                        ToolUseBlock(
                            id=block_dict.get("id", ""),
                            name=block_dict.get("name", ""),
                            input=block_dict.get("input", {}),
                        )
                    )
                elif btype == "tool_result":
                    content_blocks.append(
                        ToolResultBlock(
                            tool_use_id=block_dict.get("tool_use_id", ""),
                            content=block_dict.get("content", ""),
                            is_error=block_dict.get("is_error", False),
                        )
                    )
            if content_blocks:
                if isinstance(m.role, str) and m.role in ("user", "assistant", "system"):
                    role: Literal["user", "assistant", "system"] = cast(
                        Literal["user", "assistant", "system"], m.role
                    )
                else:
                    role = "user"
                result.append(Message(role=role, content=content_blocks))
        return result
    except Exception:
        logger.debug("Could not load message history for session %s", session_id)
        return []


async def _get_page_context_text(
    *,
    session_id: str,
    db_session: Any | None,
) -> str | None:
    """Get the latest page context for the session."""
    try:
        import artemis.db as _db
        from artemis.floating_artemis.repository import get_latest_page_context

        if db_session is not None:
            ctx = await get_latest_page_context(db_session, session_id)
        else:
            async with _db.SessionLocal() as session:
                ctx = await get_latest_page_context(session, session_id)

        if ctx is None:
            return None
        result = f"Page: {ctx.page}"
        if ctx.ref_id:
            result += f" (ref: {ctx.ref_id})"
        return result
    except Exception:
        return None


async def _persist_messages(
    *,
    session_id: str,
    user_text: str | None,
    assistant_text: str | None,
    user_content: list[dict[str, Any]] | None,
    assistant_content: list[dict[str, Any]] | None,
    usage: Usage,
    db_session: Any | None,
) -> int | None:
    """Persist user + assistant messages to the DB.

    Returns the user message's primary key (for M3 drawer anchoring), or None
    when no user message was written or persistence failed.
    """
    try:
        import artemis.db as _db
        from artemis.floating_artemis.repository import add_message, touch_session

        async def _do_persist(session: Any) -> int | None:
            user_msg_id: int | None = None
            effective_user_content = user_content
            if effective_user_content is None and user_text is not None:
                effective_user_content = [{"type": "text", "text": user_text}]
            if effective_user_content is not None:
                user_msg = await add_message(
                    session,
                    session_id=session_id,
                    role="user",
                    content=effective_user_content,
                )
                user_msg_id = user_msg.id
            effective_assistant_content = assistant_content
            if effective_assistant_content is None and assistant_text is not None:
                effective_assistant_content = [{"type": "text", "text": assistant_text}]
            if effective_assistant_content is not None:
                await add_message(
                    session,
                    session_id=session_id,
                    role="assistant",
                    content=effective_assistant_content,
                    cost_input_tokens=usage.input_tokens,
                    cost_output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=usage.cache_creation_input_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                )
            await touch_session(session, session_id)
            await session.commit()
            return user_msg_id

        if db_session is not None:
            return await _do_persist(db_session)
        else:
            async with _db.SessionLocal() as session:
                return await _do_persist(session)
    except Exception:
        logger.debug("Could not persist messages for session %s", session_id, exc_info=True)
        return None
