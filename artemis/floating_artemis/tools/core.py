"""Core Floating Artemis tools — memory, status, filesystem, preferences, sub-agents.

Authority layers:
  1 (read-only): query_memory, list_scopes, surface_status, list_routes, read_file
  2 (idempotent write): write_memory, set_pref, propose_edit
  3 (side-effect): spawn_subagent (spends tokens, requires confirmation for Sonnet/Opus)
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from artemis.agent.types import Tool
from artemis.floating_artemis.authority import AuthorizedToolRegistry

logger = logging.getLogger(__name__)

# ── Repo-root constraint for read_file ───────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent.parent.resolve()


def _safe_repo_path(relative_path: str) -> Path | None:
    """Return an absolute path only if it is within the repo root."""
    candidate = (_REPO_ROOT / relative_path).resolve()
    try:
        candidate.relative_to(_REPO_ROOT)
        return candidate
    except ValueError:
        return None


# ── Tool implementations ──────────────────────────────────────────────────────


async def _query_memory(inp: dict[str, Any]) -> str:
    """Query the Artemis memory store for relevant observations."""
    query = inp.get("query", "")
    scope = inp.get("scope", "global:global")
    limit = int(inp.get("limit", 10))
    if scope == "all":
        scope = "global:global"

    try:
        import artemis.db as _db
        from artemis.memory.retrieval import search_observations
        from artemis.memory.schemas import Scope

        async with _db.SessionLocal() as session:
            scope_kind, scope_id = scope.split(":") if ":" in scope else (scope, "default")
            scope_obj = Scope(scope_kind=scope_kind, scope_id=scope_id)
            results = await search_observations(
                session,
                scope_set=[scope_obj],
                query=query,
                limit=limit,
            )
        if not results:
            return "No relevant memory found."
        lines = [f"[{r.scope_kind}:{r.scope_id}] {r.content}" for r in results]
        return "\n".join(lines)
    except Exception as exc:
        return f"Memory query failed: {exc}"


async def _write_memory(inp: dict[str, Any]) -> str:
    """Write an observation to the Artemis memory store."""
    content = inp.get("content", "")
    scope = inp.get("scope", "agent:floating-artemis")

    if not content:
        return "Error: content is required"

    try:
        import artemis.db as _db
        from artemis.memory.schemas import Scope
        from artemis.memory.store import write_observation

        scope_kind, _, scope_id = scope.partition(":")
        scope_obj = Scope(scope_kind=scope_kind, scope_id=scope_id or "default")

        async with _db.SessionLocal() as session, session.begin():
            await write_observation(session, scope=scope_obj, content=content)
        return f"Memory written to {scope}."
    except Exception as exc:
        return f"Memory write failed: {exc}"


async def _list_scopes(inp: dict[str, Any]) -> str:  # noqa: ARG001
    """List available memory scopes."""
    try:
        from sqlalchemy import text

        import artemis.db as _db

        async with _db.SessionLocal() as session:
            result = await session.execute(
                text(
                    "SELECT scope_kind, scope_id FROM memory_scopes ORDER BY scope_kind, scope_id LIMIT 100"
                )
            )
            rows = result.fetchall()
        if not rows:
            return "No memory scopes found."
        return "\n".join(f"{r[0]}:{r[1]}" for r in rows)
    except Exception as exc:
        return f"List scopes failed: {exc}"


async def _surface_status(inp: dict[str, Any]) -> str:  # noqa: ARG001
    """Return current surface availability from /api/_status."""
    try:
        from artemis.routes.status import get_status

        status = await get_status()
        available = status.get("available_surfaces", [])
        unavailable = status.get("unavailable_surfaces", [])
        return json.dumps({"available": available, "unavailable": unavailable})
    except Exception as exc:
        return f"Status check failed: {exc}"


async def _list_routes(inp: dict[str, Any]) -> str:  # noqa: ARG001
    """List all mounted API routes in the Artemis server."""
    try:
        from artemis.main import app

        routes = []
        for route in app.routes:
            if hasattr(route, "methods") and hasattr(route, "path"):
                methods = sorted(route.methods or [])
                routes.append(f"{','.join(methods)} {route.path}")
        return "\n".join(sorted(routes)) if routes else "No routes found."
    except Exception as exc:
        return f"Route listing failed: {exc}"


async def _read_file(inp: dict[str, Any]) -> str:
    """Read a file within the repository root (path-constrained)."""
    relative = inp.get("path", "")
    if not relative:
        return "Error: path is required"

    safe_path = _safe_repo_path(relative)
    if safe_path is None:
        return f"Error: path '{relative}' is outside the repository root — access denied"

    if not safe_path.exists():
        return f"Error: '{relative}' does not exist"
    if not safe_path.is_file():
        return f"Error: '{relative}' is not a file"

    try:
        content = safe_path.read_text(encoding="utf-8", errors="replace")
        max_chars = int(inp.get("max_chars", 8000))
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n[...truncated at {max_chars} chars]"
        return content
    except Exception as exc:
        return f"Read failed: {exc}"


async def _propose_edit(inp: dict[str, Any]) -> str:
    """Propose a file edit — creates a proposal record (layer-2, no actual write)."""
    path = inp.get("path", "")
    description = inp.get("description", "")
    diff = inp.get("diff", "")
    if not path or not description:
        return "Error: path and description are required"
    # Layer 2: idempotent — just records the proposal intent; no FS write.
    proposal = {
        "type": "file_edit_proposal",
        "path": path,
        "description": description,
        "diff": diff,
    }
    return f"Edit proposed:\n{json.dumps(proposal, indent=2)}"


async def _set_pref(inp: dict[str, Any]) -> str:
    """Set an operator preference in memory."""
    key = inp.get("key", "")
    value = inp.get("value")
    if not key:
        return "Error: key is required"

    content = f"Preference: {key} = {json.dumps(value)}"
    return await _write_memory(
        {"content": content, "scope": "agent:floating-artemis", "source": "operator"}
    )


# ── Tool definitions ──────────────────────────────────────────────────────────


QUERY_MEMORY = Tool(
    name="query_memory",
    description=(
        "Query the Artemis persistent memory store for observations matching a query. "
        "Returns relevant memory snippets. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Natural language query"},
            "scope": {
                "type": "string",
                "description": "Memory scope filter (e.g., 'global:global', 'agent:floating-artemis')",
                "default": "global:global",
            },
            "limit": {"type": "integer", "description": "Max results", "default": 10},
        },
        "required": ["query"],
    },
)

WRITE_MEMORY = Tool(
    name="write_memory",
    description="Write an observation to the Artemis memory store. [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The observation to record"},
            "scope": {
                "type": "string",
                "description": "Target scope",
                "default": "agent:floating-artemis",
            },
            "source": {"type": "string", "description": "Source tag", "default": "agent"},
        },
        "required": ["content"],
    },
)

LIST_SCOPES = Tool(
    name="list_scopes",
    description="List all available memory scopes in the system. [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

SURFACE_STATUS = Tool(
    name="surface_status",
    description="Return the current surface availability map from /api/_status. [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

LIST_ROUTES = Tool(
    name="list_routes",
    description="List all mounted API routes in the Artemis server. [layer:1]",
    input_schema={"type": "object", "properties": {}, "required": []},
)

READ_FILE = Tool(
    name="read_file",
    description=(
        "Read a file within the repository root. Path must be relative to the repo root. "
        "Access is constrained to the repo root — no path traversal. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path from repo root"},
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return",
                "default": 8000,
            },
        },
        "required": ["path"],
    },
)

PROPOSE_EDIT = Tool(
    name="propose_edit",
    description="Propose a file edit (does not apply it — creates a reviewable proposal). [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to repo root"},
            "description": {"type": "string", "description": "What the edit does"},
            "diff": {"type": "string", "description": "Optional diff/patch content"},
        },
        "required": ["path", "description"],
    },
)

SET_PREF = Tool(
    name="set_pref",
    description="Set an operator preference in memory (key/value). [layer:2]",
    input_schema={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Preference key"},
            "value": {"description": "Preference value (any JSON type)"},
        },
        "required": ["key", "value"],
    },
)


# ── Model alias map ───────────────────────────────────────────────────────────

_MODEL_ALIASES: dict[str, str] = {
    "haiku": "claude-haiku-4-5",
    "sonnet": "claude-sonnet-4-6",
}

_SUBAGENT_SYSTEM = (
    "You are a helper sub-agent. Complete the requested task and return your output "
    "as a clear final message. Do not propose anything; just execute."
)


async def _spawn_subagent(inp: dict[str, Any]) -> str:
    """Spawn a one-shot ephemeral sub-agent.

    Calls F1 run_turn with a temporary message list. Creates an agent_runs row
    with is_ephemeral=True and agent_id=None for cost tracking. Returns the
    sub-agent's final text output.

    This is SPAWN (do, return, disappear) — NOT propose (save, persist, reuse).
    """
    task = inp.get("task", "").strip()
    if not task:
        return "Error: task is required"

    raw_model = inp.get("model") or "haiku"
    model = _MODEL_ALIASES.get(raw_model, raw_model)
    max_turns = int(inp.get("max_turns", 8))

    run_id = str(uuid.uuid4())

    # ── 1. Record run start ───────────────────────────────────────────────────
    try:
        import artemis.db as _db
        from artemis.builders import repository as builder_repo

        async with _db.SessionLocal() as db_session:
            await builder_repo.create_agent_run(
                db_session,
                run_id=run_id,
                agent_id=None,
                user_message=task,
                status="running",
                is_ephemeral=True,
            )
            await db_session.commit()
    except Exception as exc:
        logger.warning("spawn_subagent: could not record run start: %s", exc)

    # ── 2. Run the F1 loop ────────────────────────────────────────────────────
    output: str = ""
    cost_input = 0
    cost_output = 0
    final_status = "completed"
    error_msg: str | None = None

    try:
        from artemis.agent.loop import run_turn, user_message
        from artemis.providers.resolver import NoProviderAvailableError, resolve_adapter

        try:
            adapter = resolve_adapter(provider="claude-code")
        except NoProviderAvailableError as exc:
            raise RuntimeError(f"spawn_subagent: no provider available: {exc}") from exc

        result = await run_turn(
            adapter=adapter,
            messages=[user_message(task)],
            system=_SUBAGENT_SYSTEM,
            model=model,
            max_iterations=max_turns,
        )
        cost_input = result.usage.input_tokens
        cost_output = result.usage.output_tokens

        # Extract final text from the last assistant message
        from artemis.agent.types import TextBlock

        for msg in reversed(result.messages):
            if msg.role == "assistant":
                texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
                if texts:
                    output = " ".join(texts)
                    break
        if not output:
            output = "(sub-agent produced no text output)"
    except Exception as exc:
        logger.exception("spawn_subagent run_turn failed run_id=%s", run_id)
        final_status = "failed"
        error_msg = str(exc)
        output = f"Sub-agent failed: {exc}"

    # ── 3. Record run completion ──────────────────────────────────────────────
    try:
        import artemis.db as _db
        from artemis.builders import repository as builder_repo

        async with _db.SessionLocal() as db_session:
            await builder_repo.set_agent_run_completed(
                db_session,
                run_id,
                status=final_status,
                cost_input_tokens=cost_input,
                cost_output_tokens=cost_output,
                error=error_msg,
            )
            await db_session.commit()
    except Exception as exc:
        logger.warning("spawn_subagent: could not record run completion: %s", exc)

    return json.dumps(
        {
            "ok": final_status == "completed",
            "output": output,
            "run_id": run_id,
            "cost_usd": None,  # token-cost USD rollup deferred to a separate slice
        }
    )


SPAWN_SUBAGENT = Tool(
    name="spawn_subagent",
    description=(
        "Spawn a one-shot ephemeral helper sub-agent to complete a bounded task. "
        "The helper runs, returns its result, and disappears — no persistent artifact "
        "is created in the builders surface. Use SPAWN for one-time tasks (write code, "
        "audit a thing, summarize, generate fixtures). Use propose_agent when you want "
        "something the operator will reuse. Costs tokens; requires operator confirmation. "
        "[layer:3]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "The task for the sub-agent to complete.",
            },
            "model": {
                "type": "string",
                "enum": ["haiku", "sonnet"],
                "description": "Model to use. 'haiku' is faster and cheaper; 'sonnet' for complex tasks.",
                "default": "haiku",
            },
            "max_turns": {
                "type": "integer",
                "description": "Maximum model iterations (default 8, max 20).",
                "default": 8,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["task"],
    },
)


def register_core_tools(registry: AuthorizedToolRegistry) -> None:
    """Register all core tools into the provided registry."""
    registry.register(QUERY_MEMORY, _query_memory, layer=1)
    registry.register(WRITE_MEMORY, _write_memory, layer=2)
    registry.register(LIST_SCOPES, _list_scopes, layer=1)
    registry.register(SURFACE_STATUS, _surface_status, layer=1)
    registry.register(LIST_ROUTES, _list_routes, layer=1)
    registry.register(READ_FILE, _read_file, layer=1)
    registry.register(PROPOSE_EDIT, _propose_edit, layer=2)
    registry.register(SET_PREF, _set_pref, layer=2)
    registry.register(SPAWN_SUBAGENT, _spawn_subagent, layer=3)
