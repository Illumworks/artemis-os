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
from artemis.floating_artemis.authority import (
    AuthorizedToolRegistry,
    PendingConfirmation,
    confirmation_store,
)
from artemis.floating_artemis.intent import IntentKind, classify_intent, handle_observability_intent
from artemis.floating_artemis.memory_read_cache import put as cache_put
from artemis.floating_artemis.personality import PERSONALITY_PROFILE, select_voice_samples
from artemis.floating_artemis.schemas import MemoryObservationDigest, MemoryReadEvent
from artemis.floating_artemis.tools.builders import register_builders_tools
from artemis.floating_artemis.tools.core import register_core_tools
from artemis.floating_artemis.tools.granola_tools import register_granola_tools
from artemis.floating_artemis.tools.jira_tools import register_jira_tools
from artemis.floating_artemis.tools.marketing import register_marketing_tools
from artemis.floating_artemis.tools.okr import register_okr_tools
from artemis.floating_artemis.tools.system import register_system_tools
from artemis.floating_artemis.tools.writing_rules import register_writing_rules_tools
from artemis.integrations.gcal.tools import register_gcal_tools
from artemis.integrations.slack.tools import register_slack_tools
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
    session_id: str = "",
) -> str:
    # Lead with the high-priority distilled persona rules.
    parts = [_PERSONA_CORE]

    # Append the full personality profile as richer background detail.
    if PERSONALITY_PROFILE:
        parts.append("## Full personality profile (background reference)\n" + PERSONALITY_PROFILE)

    if voice_samples:
        samples_text = "\n".join(f'- "{line}"' for line in voice_samples)
        parts.append(
            "## Characteristic phrases (use sparingly)\n"
            "These are drawn from your voice corpus. Use them when they naturally fit — "
            "never force them:\n" + samples_text
        )

    if page_context:
        parts.append(f"## Current operator context\n{page_context}")

    if available_surfaces:
        surfaces_str = ", ".join(sorted(available_surfaces))
        parts.append(f"## Available surfaces (your tools are gated by these)\n{surfaces_str}")

    # Slack-originated session: establish the conversational context.
    if session_id.startswith("slack-"):
        parts.append(
            "**You are responding in Slack.** The operator @-mentioned you directly. "
            "**Assume they are addressing you and respond on-topic.** "
            'Do not ask "Are you talking to me?" — they are. '
            "Be concise; Slack rewards short replies."
        )

    return "\n\n".join(parts)


# ── Tool registry factory ─────────────────────────────────────────────────────


def _build_tool_registry(available_surfaces: set[str]) -> AuthorizedToolRegistry:
    registry = AuthorizedToolRegistry()
    register_core_tools(registry)
    register_builders_tools(registry)
    register_system_tools(registry)
    if "okr" in available_surfaces:
        register_okr_tools(registry)
    if "writing-rules" in available_surfaces:
        register_writing_rules_tools(registry)
    if "marketing-os" in available_surfaces or "signal-queue" in available_surfaces:
        register_marketing_tools(registry)
    register_slack_tools(registry)
    register_gcal_tools(registry)
    if "jira-board" in available_surfaces:
        register_jira_tools(registry)
    if "meetings" in available_surfaces:
        register_granola_tools(registry)
    return registry


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


# ── Main entry point ──────────────────────────────────────────────────────────


async def handle_turn(
    *,
    session_id: str,
    user_text: str,
    adapter: ModelAdapter | None = None,
    owner_user_id: int | None = None,
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
            await _persist_messages(
                session_id=session_id,
                user_text=user_text,
                assistant_text=response,
                usage=Usage(),
                db_session=db_session,
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
    system_prompt = _build_system_prompt(
        voice_samples=voice_samples,
        page_context=page_context_text,
        available_surfaces=sorted(available_surfaces),
        session_id=session_id,
    )

    # ── 4. Load history ───────────────────────────────────────────────────────
    history = await _load_message_history(session_id=session_id, db_session=db_session)

    # ── 5. Build tool registry ────────────────────────────────────────────────
    auth_registry = _build_tool_registry(available_surfaces)

    # Build a plain ToolRegistry that wraps the authorized one for the loop.
    # Layer 3/4 tools get wrapped so we can intercept before execution.
    tool_registry = _build_intercepting_tool_registry(auth_registry, session_id)

    # ── 6. Broadcast turn started ─────────────────────────────────────────────
    await _broadcast(
        session_id, {"type": "floating_artemis.turn_started", "session_id": session_id}
    )

    # ── 7. Append user message and run ────────────────────────────────────────
    messages = history + [user_message(user_text)]

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

    try:
        result = await run_turn(
            adapter=adapter,
            messages=messages,
            tools=tool_registry,
            system=system_prompt,
            hooks=hooks,
        )
    except _PendingConfirmationError as pending_exc:
        # A layer-3/4 tool was encountered — yield to operator.
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
        # Persist messages up to this point (user message + partial assistant)
        await _persist_messages(
            session_id=session_id,
            user_text=user_text,
            assistant_text=None,
            usage=Usage(),
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

    # ── 8. Extract final text ─────────────────────────────────────────────────
    response_text: str | None = None
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
            if texts:
                response_text = " ".join(texts)
                break

    # ── 9. Persist messages ────────────────────────────────────────────────────
    await _persist_messages(
        session_id=session_id,
        user_text=user_text,
        assistant_text=response_text,
        usage=result.usage,
        db_session=db_session,
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

    # Load current history and append the tool_result
    history = await _load_message_history(session_id=session_id, db_session=db_session)

    # Append a user message containing the tool_result (protocol: tool results are user-role)
    tool_result_msg = Message(role="user", content=[tool_result_block])
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
        self.tool_use_id = tool_use_id
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.layer = layer


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
                import uuid

                tool_use_id = str(uuid.uuid4())
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


async def _get_voice_samples(
    *,
    session_id: str,
    db_session: Any | None,
    count: int = 5,
) -> list[str]:
    """Sample voice lines for system prompt injection."""
    try:
        import artemis.db as _db
        from artemis.floating_artemis.repository import sample_voice_lines

        if db_session is not None:
            lines = await sample_voice_lines(db_session, count=count)
        else:
            async with _db.SessionLocal() as session:
                lines = await sample_voice_lines(session, count=count)
        return [line.line for line in lines]
    except Exception:
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
    usage: Usage,
    db_session: Any | None,
) -> None:
    """Persist user + assistant messages to the DB."""
    try:
        import artemis.db as _db
        from artemis.floating_artemis.repository import add_message, touch_session

        async def _do_persist(session: Any) -> None:
            if user_text is not None:
                await add_message(
                    session,
                    session_id=session_id,
                    role="user",
                    content=[{"type": "text", "text": user_text}],
                )
            if assistant_text is not None:
                await add_message(
                    session,
                    session_id=session_id,
                    role="assistant",
                    content=[{"type": "text", "text": assistant_text}],
                    cost_input_tokens=usage.input_tokens,
                    cost_output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=usage.cache_creation_input_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                )
            await touch_session(session, session_id)
            await session.commit()

        if db_session is not None:
            await _do_persist(db_session)
        else:
            async with _db.SessionLocal() as session:
                await _do_persist(session)
    except Exception:
        logger.debug("Could not persist messages for session %s", session_id, exc_info=True)
