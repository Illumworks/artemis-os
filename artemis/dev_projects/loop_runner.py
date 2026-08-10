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


async def _forge_append_log(run_id: str, kind: str, payload: dict[str, Any]) -> None:
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
        _provider: str = dev_session.provider
        _bypass: bool = dev_session.bypass_permissions
        _forge_mode: str | None = dev_session.forge_mode
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

    # --- 2. Determine write mode ---
    write_mode = _forge_mode == "write"

    # --- 3. Create ForgeRun (failure-isolated) ---
    run_id = f"run_{uuid.uuid4().hex}"
    await _forge_create_run(run_id, dev_session_id=session_id, project_id=_project_id)

    # --- 4. Serialize turns per session; compute effective working path ---
    # Lazy imports to avoid circular import issues at module load time.
    from artemis.dev_projects.worktree import (  # noqa: PLC0415
        WorktreeError,
        ensure_worktree,
        session_lock,
    )

    async with session_lock(session_id):
        # Resolve the effective project path for this turn.
        if write_mode:
            try:
                effective_path = await ensure_worktree(project.path, session_id)
            except WorktreeError as wt_exc:
                logger.error(
                    "forge write-mode: could not create worktree for session=%s: %s",
                    session_id,
                    wt_exc,
                )
                await _forge_append_log(run_id, "error", {"text": str(wt_exc)})
                await _persist_and_broadcast(
                    session_id,
                    role="assistant",
                    content=[
                        {
                            "type": "text",
                            "text": (
                                f"Forge write mode could not create the isolated worktree: {wt_exc}"
                            ),
                        }
                    ],
                    event_type="dev_projects.error",
                )
                await _forge_complete_run(run_id, status="failed", error=str(wt_exc))
                return
        else:
            effective_path = project.path

        # --- 5. Execute the turn, complete/fail the run ---
        turn_exc: BaseException | None = None
        try:
            # The legacy keyword-heuristic bash stub (_maybe_run_local_tool) is a
            # pre-Ares placeholder. For claude-code sessions Ares drives the turn with
            # his own real, scope-safe tools (list_project_dir, git_status, etc.), so
            # the stub must NOT intercept -- its naive "list/file/run" keyword match
            # would otherwise hijack the turn and raise a permission gate. Only the
            # non-Ares (other-provider) path still uses the stub.
            handled = False
            if _provider != "claude-code":
                handled = await _maybe_run_local_tool(
                    session_id=session_id,
                    project_path=effective_path,
                    user_text=user_text,
                    bypass=_bypass,
                    run_id=run_id,
                )
            if not handled:
                await _run_provider_completion(
                    session_id=session_id,
                    run_id=run_id,
                    project_path=effective_path,
                    write_mode=write_mode,
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
    *,
    session_id: int,
    project_path: str,
    user_text: str,
    bypass: bool,
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
    *,
    session_id: int,
    run_id: str | None = None,
    project_path: str | None = None,
    write_mode: bool = False,
) -> None:
    """Execute one Forge turn.

    For the ``claude-code`` provider: sets the Forge context vars and calls
    the adapter once via ``_run_forge_turn``.  The adapter's
    ``_complete_with_tools`` branch runs the real Claude Code CLI inside the
    project directory.  When ``write_mode`` is True the adapter is granted
    Write/Edit/Bash in addition to the read-only tools, and ``project_path``
    is the isolated worktree path (not the real project tree).

    For all other providers: falls through to the legacy bare-completion path
    (streaming or non-streaming), unchanged from Phase 1.

    Fallback: if ``_run_forge_turn`` fails for any reason, the function logs
    the error and retries using the legacy bare path so the turn is never
    silently dropped.  EXCEPTION: when write_mode is True the fallback is
    NOT used -- a failure in the forge-turn path must not silently fall back
    to editing the real project tree.
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
    # Forge turn path (claude-code provider only)
    # -----------------------------------------------------------------------
    if provider == "claude-code":
        try:
            await _run_forge_turn(
                session_id=session_id,
                run_id=run_id,
                project_path=project_path,
                request_messages=request_messages,
                model=model,
                write_mode=write_mode,
            )
            return
        except Exception:
            if write_mode:
                # In write mode the worktree is already set up -- falling back
                # to the legacy bare path would allow writes to the real project
                # tree, which defeats isolation.  Re-raise so the caller marks
                # the run failed.
                raise
            logger.exception(
                "forge turn: _run_forge_turn failed for session=%s run=%s -- falling back to bare completion",
                session_id,
                run_id,
            )
            # Fall through to the legacy path below (read-only sessions only).

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


async def _run_forge_turn(
    *,
    session_id: int,
    run_id: str | None,
    project_path: str | None,
    request_messages: list[Message],
    model: str | None,
    write_mode: bool = False,
) -> None:
    """Run one Forge turn via the real Claude Code CLI.

    Routing through the Forge branch
    ---------------------------------
    ``ClaudeCodeAdapter.complete()`` routes to ``_complete_with_tools()`` when
    ``request.tools`` is non-empty.  Inside ``_complete_with_tools``, if
    ``forge_project_path_var`` is set, the method builds a Forge argv
    (``--add-dir``, ``--permission-mode bypassPermissions``) and runs the CLI
    with ``cwd=project_path`` -- no Artemis MCP server is launched.  When
    ``forge_write_mode_var`` is also True the adapter grants Write/Edit/Bash
    in addition to the read-only tools.

    The tools list itself is ignored by the Forge argv; it is only present to
    trigger the ``_complete_with_tools`` dispatch.  A single placeholder Tool
    object satisfies the guard.

    When ``project_path`` is None, this function raises so the caller falls
    back to the legacy bare-completion path (read-only sessions only) -- Forge
    mode requires a directory.

    All imports are lazy to guard against circular imports with providers/
    floating_artemis modules.  Any exception propagates to the caller.

    write_mode
    ----------
    When True:
      - ``forge_write_mode_var`` is set to True so the adapter unlocks Write,
        Edit, and Bash.
      - ``project_path`` is the worktree path (already resolved by the caller).
      - The Ares system prompt is extended with a short write-mode instruction.
    When False (default): read-only behavior identical to before this change.
    """
    if project_path is None:
        raise RuntimeError("forge turn: project_path is None -- cannot enter Forge mode")

    # -- Lazy imports (circular-import guard) --------------------------------
    from artemis.agent.types import Tool as ATool
    from artemis.dev_projects.context import forge_project_path_var, forge_write_mode_var
    from artemis.floating_artemis.context import floating_session_id_var
    from artemis.floating_artemis.personality import load_agent_profile
    from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

    # -- 1. Resolve the claude-code adapter ----------------------------------
    try:
        adapter = resolve_adapter(provider="claude-code")
    except NoProviderAvailableError as exc:
        raise RuntimeError(f"forge turn: no claude-code adapter available: {exc}") from exc

    # -- 2. Build Ares system prompt (persona_core + profile_text) -----------
    ares_profile = load_agent_profile("ares")
    parts: list[str] = []
    if ares_profile.persona_core:
        parts.append(ares_profile.persona_core)
    if ares_profile.profile_text:
        parts.append(ares_profile.profile_text)
    ares_system = "\n\n".join(parts) or "You are Ares, a build assistant."

    # In write mode, append a brief instruction scoping Ares to the worktree.
    if write_mode:
        ares_system += (
            "\n\nYou are working in an isolated git worktree on branch "
            f"forge/session-{session_id}. You may read, edit, run commands, and "
            "commit freely inside this worktree. Do NOT push or merge to any other "
            "branch -- the human reviews this branch and merges it manually."
        )

    # -- 3. Build CompletionRequest ------------------------------------------
    # ``tools`` must be non-empty so ``adapter.complete()`` routes to
    # ``_complete_with_tools()`` where the Forge branch lives.  The placeholder
    # tool is never called -- the Forge argv uses native tools only.
    placeholder_tools: list[ATool] = [
        ATool(
            name="forge_noop",
            description="Internal placeholder; not callable. Triggers Forge mode routing.",
            input_schema={"type": "object", "properties": {}},
        )
    ]
    effective_model = model or _ARES_FORGE_MODEL_DEFAULT
    request = CompletionRequest(
        messages=request_messages,
        system=ares_system,
        tools=placeholder_tools,
        model=effective_model,
    )

    # -- 4. Set contextvars (set/reset token pattern in try/finally) ---------
    # forge_project_path_var  -> tells _complete_with_tools to use Forge argv
    #                            (in write mode this is the worktree path)
    # forge_write_mode_var    -> grants Write/Edit/Bash when True
    # floating_session_id_var -> stable session identity for safety/parity
    forge_token = forge_project_path_var.set(project_path)
    write_token = forge_write_mode_var.set(write_mode)
    floating_token = floating_session_id_var.set(f"forge-{session_id}")
    try:
        # -- 5. Single adapter.complete() call -- claude-code runs its own loop
        response = await adapter.complete(request)
    finally:
        forge_project_path_var.reset(forge_token)
        forge_write_mode_var.reset(write_token)
        floating_session_id_var.reset(floating_token)

    # -- 6. Extract final text -----------------------------------------------
    from artemis.agent.types import TextBlock as ATextBlock

    final_text = (
        " ".join(b.text for b in response.message.content if isinstance(b, ATextBlock)).strip()
        or "(No response.)"
    )

    # -- 7. Persist + broadcast via the existing Phase-1 path ---------------
    final_content: list[dict[str, Any]] = [{"type": "text", "text": final_text}]
    await _persist_and_broadcast(
        session_id,
        role="assistant",
        content=final_content,
        event_type="dev_projects.message_complete",
    )
    # Log to durable ForgeRunLog so reconnect replay captures the result.
    if run_id is not None:
        await _forge_append_log(
            run_id,
            "message",
            {"role": "assistant", "content": final_content},
        )


# Keep the old name as an alias so any external callers that may reference it
# do not break at import time.  _run_provider_completion calls _run_forge_turn
# directly; _run_ares_loop is retained as a shim only.
_run_ares_loop = _run_forge_turn


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
