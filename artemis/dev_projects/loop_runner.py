"""Dev Projects turn runner.

The provider registry is the model boundary. A small permission-gated local
tool path handles the in-app Claude Code style approval loop for shell/listing
operations until CLI adapters expose structured tool-use events.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from artemis.agent.client import CompletionRequest, SupportsStreaming
from artemis.agent.types import Message, Role, TextBlock
from artemis.db import SessionLocal
from artemis.dev_projects import repository as repo
from artemis.providers import get_adapter
from artemis.providers.streaming import StreamMessageStop, StreamTextDelta
from artemis.ws.manager import ws_manager

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


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


async def run_turn(
    session_id: int, user_text: str, images: list[dict[str, Any]] | None = None
) -> None:
    async with SessionLocal() as db:
        dev_session = await repo.get_session(db, session_id)
        project = await repo.get_project(db, dev_session.project_id)
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

    try:
        handled = await _maybe_run_local_tool(
            session_id=session_id,
            project_path=project.path,
            user_text=user_text,
            bypass=dev_session.bypass_permissions,
        )
        if handled:
            return
        await _run_provider_completion(session_id=session_id)
    except Exception as exc:
        await _persist_and_broadcast(
            session_id,
            role="assistant",
            content=[{"type": "text", "text": f"Error: {exc}"}],
            event_type="dev_projects.error",
        )


async def _maybe_run_local_tool(
    *, session_id: int, project_path: str, user_text: str, bypass: bool
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
        await broadcast(
            session_id,
            {
                "type": "dev_projects.permission_required",
                "permission_id": permission_id,
                "tool_name": "bash",
                "args": {"command": command, "cwd": project_path},
            },
        )
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


async def _run_provider_completion(*, session_id: int) -> None:
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

    adapter = get_adapter(provider)
    request = CompletionRequest(messages=request_messages, model=model)
    text_parts: list[str] = []

    if isinstance(adapter, SupportsStreaming):
        stream = await adapter.stream(request)
        async for event in stream:
            if isinstance(event, StreamTextDelta):
                text_parts.append(event.text)
                await broadcast(session_id, {"type": "dev_projects.token", "token": event.text})
            elif isinstance(event, StreamMessageStop):
                break
    else:
        response = await adapter.complete(request)
        text = "\n".join(
            block.text for block in response.message.content if isinstance(block, TextBlock)
        )
        if text:
            text_parts.append(text)
            await broadcast(session_id, {"type": "dev_projects.token", "token": text})

    final_text = "".join(text_parts).strip() or "(No response.)"
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
