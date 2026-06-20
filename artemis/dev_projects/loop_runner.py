"""Dev Projects turn runner.

The provider registry is the model boundary. A small permission-gated local
tool path handles the in-app Claude Code style approval loop for shell/listing
operations until CLI adapters expose structured tool-use events.

Forge run tracking (Phase 1, chunk 1.2)
----------------------------------------
Every call to run_turn creates a ForgeRun row and appends ephemeral streaming
events to ForgeRunLog so a device reconnecting mid-build can replay what was
streamed.  The bookkeeping is fully failure-isolated: any exception inside the
tracking helpers is caught, logged, and the actual build turn continues.

Log kinds and payload shapes (frontend contract):
  kind="token"      {"text": "<batched_text>"}   -- streamed text batches (legacy/fallback)
  kind="message"    {"role": "assistant", "content": [...]}  -- Ares full message per iteration
  kind="tool_use"   {"tool": str, "input": {...}}            -- Ares tool call
  kind="tool_result" {"tool": str, "result": str, "is_error": bool}  -- Ares tool result
  kind="permission" {"permission_id": str, "tool_name": str, "args": dict}
  kind="error"      {"text": "<error_message>"}

WebSocket event types (Forge Phase 2, chunk 2.2):
  dev_projects.token          -- legacy streaming token (non-Ares path)
  dev_projects.message        -- full assistant message per Ares iteration
  dev_projects.tool_step      -- Ares tool_use or tool_result step
  dev_projects.message_complete  -- final persisted assistant message (unchanged)
  dev_projects.permission_required  -- local tool permission gate

WebSocket payload shapes (frontend contract for chunk 2.3):
  dev_projects.message:
    {"type": "dev_projects.message", "role": "assistant",
     "content": [{"type": "text", "text": str} | {"type": "tool_use", ...}]}

  dev_projects.tool_step (tool call):
    {"type": "dev_projects.tool_step", "tool": str, "input": {...}}

  dev_projects.tool_step (tool result):
    {"type": "dev_projects.tool_step", "tool": str, "result": str,
     "is_error": bool, "is_result": true}
    (result text is capped at 4000 chars before broadcast/log)
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from artemis.agent.client import CompletionRequest, SupportsStreaming
from artemis.agent.types import Message, Role, TextBlock
from artemis.db import SessionLocal
from artemis.dev_projects import repository as repo
from artemis.providers import get_adapter
from artemis.providers.streaming import StreamMessageStop, StreamTextDelta
from artemis.ws.manager import ws_manager

logger = logging.getLogger(__name__)

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]

# Token-batching thresholds
_TOKEN_BATCH_SIZE = 50  # flush after this many tokens


@dataclass(slots=True)
class PendingPermission:
    session_id: int
    permission_id: str
    tool_name: str
    args: dict[str, Any]
    future: asyncio.Future[bool]


_PENDING: dict[str, PendingPermission] = {}


def _room(session_id: int) -> str:
    return f"dev-projects:{session_id}"


async def broadcast(session_id: int, event: dict[str, Any]) -> None:
    await ws_manager.broadcast(_room(session_id), event)


def pending_permission(permission_id: str) -> PendingPermission | None:
    return _PENDING.get(permission_id)


async def decide_permission(*, session_id: int, permission_id: str, approved: bool) -> bool:
    pending = _PENDING.get(permission_id)
    if pending is None or pending.session_id != session_id:
        return False
    if not pending.future.done():
        pending.future.set_result(approved)
    return True


# ---------------------------------------------------------------------------
# Forge run bookkeeping helpers (all failure-isolated)
# ---------------------------------------------------------------------------


async def _forge_create_run(run_id: str, dev_session_id: int, project_id: int) -> None:
    """Create a ForgeRun row. Swallows all exceptions."""
    try:
        from artemis.dev_projects.forge_runs import create_run  # lazy import

        async with SessionLocal() as db:
            await create_run(
                db,
                run_id=run_id,
                dev_session_id=dev_session_id,
                project_id=project_id,
            )
            await db.commit()
    except Exception:
        logger.exception("forge_run: create_run failed for run_id=%s (non-fatal)", run_id)


async def _forge_append_log(
    run_id: str, kind: str, payload: dict[str, Any]
) -> None:
    """Append one event to ForgeRunLog. Swallows all exceptions."""
    try:
        from artemis.dev_projects.forge_runs import append_log  # lazy import

        async with SessionLocal() as db:
            await append_log(db, run_id=run_id, kind=kind, payload=payload)
            await db.commit()
    except Exception:
        logger.exception(
            "forge_run: append_log failed for run_id=%s kind=%s (non-fatal)", run_id, kind
        )


async def _forge_complete_run(
    run_id: str, status: str = "completed", error: str | None = None
) -> None:
    """Mark a ForgeRun completed/failed. Swallows all exceptions."""
    try:
        from artemis.dev_projects.forge_runs import complete_run  # lazy import

        async with SessionLocal() as db:
            await complete_run(db, run_id=run_id, status=status, error=error)
            await db.commit()
    except Exception:
        logger.exception(
            "forge_run: complete_run failed for run_id=%s status=%s (non-fatal)", run_id, status
        )


# ---------------------------------------------------------------------------
# Token-batch helper (stateful, lives for the duration of one stream)
# ---------------------------------------------------------------------------


@dataclass
class _TokenBatch:
    """Accumulates tokens and flushes to ForgeRunLog in batches."""

    run_id: str
    _buf: list[str] = field(default_factory=list)

    async def push(self, token: str) -> None:
        self._buf.append(token)
        if len(self._buf) >= _TOKEN_BATCH_SIZE:
            await self.flush()

    async def flush(self) -> None:
        if not self._buf:
            return
        text = "".join(self._buf)
        self._buf.clear()
        await _forge_append_log(self.run_id, "token", {"text": text})


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_turn(
    session_id: int, user_text: str, images: list[dict[str, Any]] | None = None
) -> None:
    # --- 1. Persist user message (existing logic) ---
    async with SessionLocal() as db:
        dev_session = await repo.get_session(db, session_id)
        project = await repo.get_project(db, dev_session.project_id)
        # Capture scalar values before the session closes
        _project_id: int = dev_session.project_id
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        content.extend(images or [])
        user_msg = await repo.add_message(db, session_id=session_id, role="user", content=content)
        await db.commit()

    await broadcast(
        session_id,
        {
            "type": "dev_projects.message",
            "message": {
                "id": user_msg.id,
                "session_id": session_id,
                "role": "user",
                "content": content,
                "created_at": user_msg.created_at.isoformat(),
            },
        },
    )

    # --- 2. Create ForgeRun (failure-isolated) ---
    run_id = f"run_{uuid.uuid4().hex}"
    await _forge_create_run(run_id, dev_session_id=session_id, project_id=_project_id)

    # --- 3. Execute the turn, complete/fail the run ---
    turn_exc: BaseException | None = None
    try:
        # The legacy keyword-heuristic bash stub (_maybe_run_local_tool) is a
        # pre-Ares placeholder. For claude-code sessions Ares drives the turn with
        # his own real, scope-safe tools (list_project_dir, git_status, etc.), so
        # the stub must NOT intercept — its naive "list/file/run" keyword match
        # would otherwise hijack the turn and raise a permission gate. Only the
        # non-Ares (other-provider) path still uses the stub.
        handled = False
        if dev_session.provider != "claude-code":
            handled = await _maybe_run_local_tool(
                session_id=session_id,
                project_path=project.path,
                user_text=user_text,
                bypass=dev_session.bypass_permissions,
                run_id=run_id,
            )
        if not handled:
            await _run_provider_completion(
                session_id=session_id,
                run_id=run_id,
                project_path=project.path,
            )
    except Exception as exc:
        turn_exc = exc
        # Log error to ForgeRunLog before broadcasting
        await _forge_append_log(run_id, "error", {"text": str(exc)})
        await _persist_and_broadcast(
            session_id,
            role="assistant",
            content=[{"type": "text", "text": f"Error: {exc}"}],
            event_type="dev_projects.error",
        )
    finally:
        if turn_exc is None:
            await _forge_complete_run(run_id, status="completed")
        else:
            await _forge_complete_run(run_id, status="failed", error=str(turn_exc))


async def _maybe_run_local_tool(
    *, session_id: int, project_path: str, user_text: str, bypass: bool,
    run_id: str | None = None,
) -> bool:
    lower = user_text.lower()
    wants_listing = "list" in lower and ("file" in lower or "directory" in lower or "dir" in lower)
    wants_shell = lower.startswith("run ") or lower.startswith("bash ")
    if not wants_listing and not wants_shell:
        return False

    command = "find . -maxdepth 2 -type f | sed 's#^./##' | sort | head -80"
    if wants_shell:
        command = user_text.split(" ", 1)[1].strip()

    permission_id = f"perm_{uuid.uuid4().hex[:16]}"
    tool_block = {
        "type": "tool_use",
        "id": permission_id,
        "name": "bash",
        "input": {"command": command, "cwd": project_path},
    }
    await _persist_and_broadcast(session_id, role="assistant", content=[tool_block])

    if not bypass:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()
        _PENDING[permission_id] = PendingPermission(
            session_id=session_id,
            permission_id=permission_id,
            tool_name="bash",
            args={"command": command, "cwd": project_path},
            future=future,
        )
        perm_payload: dict[str, Any] = {
            "permission_id": permission_id,
            "tool_name": "bash",
            "args": {"command": command, "cwd": project_path},
        }
        await broadcast(
            session_id,
            {
                "type": "dev_projects.permission_required",
                **perm_payload,
            },
        )
        # Log the pending permission gate to ForgeRunLog
        if run_id is not None:
            await _forge_append_log(run_id, "permission", perm_payload)

        approved = await future
        _PENDING.pop(permission_id, None)
        if not approved:
            await _persist_and_broadcast(
                session_id,
                role="tool_result",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": permission_id,
                        "content": "Denied by operator.",
                        "is_error": True,
                    }
                ],
            )
            await _persist_and_broadcast(
                session_id,
                role="assistant",
                content=[
                    {"type": "text", "text": "I stopped there because the tool call was denied."}
                ],
                event_type="dev_projects.message_complete",
            )
            return True

    result = await _run_shell(command, cwd=project_path)
    await _persist_and_broadcast(
        session_id,
        role="tool_result",
        content=[
            {
                "type": "tool_result",
                "tool_use_id": permission_id,
                "content": result,
                "is_error": False,
            }
        ],
    )
    await _persist_and_broadcast(
        session_id,
        role="assistant",
        content=[{"type": "text", "text": f"Here is what I found:\n\n```text\n{result}\n```"}],
        event_type="dev_projects.message_complete",
    )
    return True


async def _run_shell(command: str, *, cwd: str) -> str:
    safe_cwd = Path(cwd).expanduser().resolve()
    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=str(safe_cwd),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    parts = []
    if out:
        parts.append(out)
    if err:
        parts.append(f"stderr:\n{err}")
    if proc.returncode:
        parts.append(f"exit code: {proc.returncode}")
    return "\n".join(parts) or "(no output)"


_ARES_FORGE_MODEL_DEFAULT = "claude-sonnet-4-5"
_ARES_TOOL_RESULT_BROADCAST_CAP = 4000  # chars; keep broadcast payloads sane


async def _run_provider_completion(
    *, session_id: int, run_id: str | None = None, project_path: str | None = None
) -> None:
    """Execute one Forge turn.

    For the ``claude-code`` provider: runs the full Ares agent loop (tool-capable,
    multi-iteration) and streams each step to the Forge WebSocket room.

    For all other providers: falls through to the legacy bare-completion path
    (streaming or non-streaming), unchanged from Phase 1.

    Fallback: if building Ares's system prompt or tool registry fails for any
    reason, the function logs the error and retries using the legacy bare path so
    the turn is never silently dropped.
    """
    async with SessionLocal() as db:
        dev_session = await repo.get_session(db, session_id)
        messages = await repo.list_messages(db, session_id, limit=50)
        request_messages: list[Message] = []
        for msg in messages:
            if msg.role not in {"user", "assistant"}:
                continue
            text = "\n".join(
                str(block.get("text") or block.get("content") or "")
                for block in msg.content
                if block.get("type") in {"text", "tool_result"}
            ).strip()
            if text:
                request_messages.append(
                    Message(role=cast(Role, msg.role), content=[TextBlock(text=text)])
                )
        provider = dev_session.provider
        model = dev_session.model

    # -----------------------------------------------------------------------
    # Ares agent-loop path (claude-code provider only)
    # -----------------------------------------------------------------------
    if provider == "claude-code":
        try:
            await _run_ares_loop(
                session_id=session_id,
                run_id=run_id,
                project_path=project_path,
                request_messages=request_messages,
                model=model,
            )
            return
        except Exception:
            logger.exception(
                "forge turn: Ares loop failed for session=%s run=%s — falling back to bare completion",
                session_id,
                run_id,
            )
            # Fall through to the legacy path below.

    # -----------------------------------------------------------------------
    # Legacy bare-completion path (non-claude-code, or Ares fallback)
    # -----------------------------------------------------------------------
    adapter = get_adapter(provider)
    request = CompletionRequest(messages=request_messages, model=model)
    text_parts: list[str] = []

    # Token batch for ForgeRunLog (only active when run_id is set)
    batch: _TokenBatch | None = _TokenBatch(run_id) if run_id is not None else None

    if isinstance(adapter, SupportsStreaming):
        stream = await adapter.stream(request)
        async for event in stream:
            if isinstance(event, StreamTextDelta):
                text_parts.append(event.text)
                # Existing live broadcast -- unchanged
                await broadcast(session_id, {"type": "dev_projects.token", "token": event.text})
                # Additive: accumulate into batch for ForgeRunLog
                if batch is not None:
                    await batch.push(event.text)
            elif isinstance(event, StreamMessageStop):
                break
        # Flush any remaining buffered tokens
        if batch is not None:
            await batch.flush()
    else:
        response = await adapter.complete(request)
        text = "\n".join(
            block.text for block in response.message.content if isinstance(block, TextBlock)
        )
        if text:
            text_parts.append(text)
            await broadcast(session_id, {"type": "dev_projects.token", "token": text})
            # Non-streaming: log as a single token batch
            if batch is not None:
                await _forge_append_log(run_id, "token", {"text": text})  # type: ignore[arg-type]

    final_text = "".join(text_parts).strip() or "(No response.)"
    await _persist_and_broadcast(
        session_id,
        role="assistant",
        content=[{"type": "text", "text": final_text}],
        event_type="dev_projects.message_complete",
    )


async def _run_ares_loop(
    *,
    session_id: int,
    run_id: str | None,
    project_path: str | None,
    request_messages: list[Message],
    model: str | None,
) -> None:
    """Run the Ares agent loop for one Forge turn.

    All imports are lazy to avoid circular import issues with providers/
    floating_artemis modules.  If any import or setup step fails the exception
    propagates to the caller, which catches it and falls back to bare completion.
    """
    # -- Lazy imports (circular-import guard) --------------------------------
    from artemis.agent.hooks import HookRegistry
    from artemis.agent.loop import run_turn as agent_run_turn
    from artemis.agent.types import TextBlock as ATextBlock
    from artemis.agent.types import ToolResultBlock as AToolResultBlock
    from artemis.agent.types import ToolUseBlock as AToolUseBlock
    from artemis.floating_artemis.personality import load_agent_profile, select_voice_samples
    from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    # -- 1. Resolve the claude-code adapter ----------------------------------
    try:
        adapter = resolve_adapter(provider="claude-code")
    except NoProviderAvailableError as exc:
        raise RuntimeError(f"forge Ares: no claude-code adapter available: {exc}") from exc

    # -- 2. Build Ares's system prompt ---------------------------------------
    ares_profile = load_agent_profile("ares")
    # Use the real _build_system_prompt from chat.py to get the full human-voice
    # and acting rules injected.  Import lazily; fall back to a minimal prompt
    # if the import somehow fails (e.g. transitive dep missing at test time).
    try:
        from artemis.floating_artemis.chat import _build_system_prompt  # noqa: PLC2701

        voice_samples = select_voice_samples(
            session_id=str(session_id),
            k=4,
            voice_corpus=ares_profile.voice_corpus,
        )
        ares_system = _build_system_prompt(
            voice_samples=voice_samples,
            page_context=None,
            available_surfaces=["dev-projects"],
            persona_core=ares_profile.persona_core,
            profile_text=ares_profile.profile_text,
            display_name=ares_profile.display_name,
            agent_id="ares",
            session_id=str(session_id),
        )
    except Exception:
        logger.warning(
            "forge Ares: _build_system_prompt import failed; using bare persona_core",
            exc_info=True,
        )
        parts = []
        if ares_profile.persona_core:
            parts.append(ares_profile.persona_core)
        if ares_profile.profile_text:
            parts.append(ares_profile.profile_text)
        ares_system = "\n\n".join(parts) or "You are Ares, a build assistant."

    # -- 3. Build Ares's tool registry ---------------------------------------
    auth_registry = build_authorized_tool_registry(
        {"dev-projects"},
        agent_id="ares",
        project_path=project_path,
    )
    # Wrap into the plain ToolRegistry the agent loop consumes.
    # Use the same auto-invoke wrapper that chat.py uses so layer-1/2 tools
    # run immediately and layer-3/4 tools are staged.
    try:
        from artemis.floating_artemis.chat import _build_auto_invoke_tool_registry  # noqa: PLC2701

        tool_registry = _build_auto_invoke_tool_registry(
            auth_registry,
            session_id=str(session_id),
            agent_id="ares",
        )
    except Exception:
        logger.warning(
            "forge Ares: _build_auto_invoke_tool_registry import failed; using empty registry",
            exc_info=True,
        )
        from artemis.agent.tools import ToolRegistry as PlainToolRegistry

        # Build a minimal plain registry from layer-1/2 entries only.
        tool_registry = PlainToolRegistry()
        for entry in auth_registry.all_entries():
            if entry.layer <= 2:
                tool_registry.register(entry.tool, entry.impl)

    # -- 4. Build hook registry to bridge loop events -> Forge WS ------------
    hooks = HookRegistry()

    # Track tool-name by tool_use_id so tool-result messages can name the tool.
    _pending_tool_names: dict[str, str] = {}

    async def _on_message_hook(msg: Any) -> None:  # noqa: ANN401
        """Fire for every new message appended by the agent loop."""
        from artemis.agent.types import Message as AMessage

        if not isinstance(msg, AMessage):
            return

        if msg.role == "assistant":
            # Collect text and tool_use blocks for broadcasting.
            text_blocks: list[dict[str, Any]] = []
            tool_use_blocks: list[dict[str, Any]] = []

            for block in msg.content:
                if isinstance(block, ATextBlock):
                    text_blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, AToolUseBlock):
                    # Remember the name so the matching result can use it.
                    _pending_tool_names[block.id] = block.name
                    tool_use_blocks.append(
                        {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
                    )

            # Broadcast the full assistant message (text + tool_use blocks together).
            all_blocks: list[dict[str, Any]] = text_blocks + tool_use_blocks
            if all_blocks:
                await broadcast(
                    session_id,
                    {
                        "type": "dev_projects.message",
                        "role": "assistant",
                        "content": all_blocks,
                    },
                )
                # Log to ForgeRunLog as a "message" kind.
                if run_id is not None:
                    await _forge_append_log(run_id, "message", {"role": "assistant", "content": all_blocks})

            # Broadcast individual tool_use steps.
            for tu in tool_use_blocks:
                await broadcast(
                    session_id,
                    {
                        "type": "dev_projects.tool_step",
                        "tool": tu["name"],
                        "input": tu["input"],
                    },
                )
                if run_id is not None:
                    await _forge_append_log(
                        run_id,
                        "tool_use",
                        {"tool": tu["name"], "input": tu["input"]},
                    )

        elif msg.role == "user":
            # Tool-result messages: one ToolResultBlock per tool call.
            for block in msg.content:
                if not isinstance(block, AToolResultBlock):
                    continue
                tool_name = _pending_tool_names.pop(block.tool_use_id, block.tool_use_id)
                # Cap result text before broadcast/log to keep payloads sane.
                raw_result = block.content if isinstance(block.content, str) else str(block.content)
                capped = raw_result[:_ARES_TOOL_RESULT_BROADCAST_CAP]
                if len(raw_result) > _ARES_TOOL_RESULT_BROADCAST_CAP:
                    capped += " ...[truncated]"
                await broadcast(
                    session_id,
                    {
                        "type": "dev_projects.tool_step",
                        "tool": tool_name,
                        "result": capped,
                        "is_error": block.is_error,
                        "is_result": True,
                    },
                )
                if run_id is not None:
                    await _forge_append_log(
                        run_id,
                        "tool_result",
                        {
                            "tool": tool_name,
                            "result": capped,
                            "is_error": block.is_error,
                        },
                    )

    hooks.on("on_message", _on_message_hook)

    # -- 5. Run the agent loop -----------------------------------------------
    effective_model = model or _ARES_FORGE_MODEL_DEFAULT
    result = await agent_run_turn(
        adapter=adapter,
        messages=request_messages,
        tools=tool_registry,
        system=ares_system,
        model=effective_model,
        max_iterations=12,
        hooks=hooks,
    )

    # -- 6. Extract final text and persist via the existing path -------------
    final_text = ""
    for msg in reversed(result.messages):
        if msg.role == "assistant":
            texts = [b.text for b in msg.content if isinstance(b, ATextBlock)]
            if texts:
                final_text = " ".join(texts)
                break

    final_text = final_text.strip() or "(No response.)"
    # Persist via the unchanged Phase-1 path so dev_messages and the
    # message_complete event are identical to the pre-Ares behaviour.
    await _persist_and_broadcast(
        session_id,
        role="assistant",
        content=[{"type": "text", "text": final_text}],
        event_type="dev_projects.message_complete",
    )


async def _persist_and_broadcast(
    session_id: int,
    *,
    role: str,
    content: list[dict[str, Any]],
    event_type: str = "dev_projects.message",
) -> None:
    async with SessionLocal() as db:
        msg = await repo.add_message(db, session_id=session_id, role=role, content=content)
        await db.commit()
    await broadcast(
        session_id,
        {
            "type": event_type,
            "message": {
                "id": msg.id,
                "session_id": session_id,
                "role": role,
                "content": content,
                "created_at": msg.created_at.isoformat(),
            },
        },
    )
