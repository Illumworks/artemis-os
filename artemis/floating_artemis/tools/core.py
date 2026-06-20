"""Core Floating Artemis tools — memory, status, filesystem, preferences, sub-agents.

Authority layers:
  1 (read-only): query_memory, list_scopes, surface_status, list_routes, read_file
  2 (idempotent write): write_memory, set_pref, propose_edit
  3 (side-effect): spawn_subagent (spends tokens, requires confirmation for Sonnet/Opus)
"""

from __future__ import annotations

import asyncio
import contextlib
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


def _safe_path_under(root: Path, relative_path: str) -> Path | None:
    """Return an absolute path only if it resolves within ``root``.

    Accepts relative paths (resolved against ``root``) and absolute paths
    (which must still fall inside ``root`` after resolution).  Returns None
    on path-traversal / escape attempts so callers never reach the filesystem
    for out-of-bounds paths.
    """
    try:
        candidate = (root / relative_path).resolve()
        candidate.relative_to(root)
        return candidate
    except (ValueError, TypeError):
        return None


# ── Forge / Ares project-scoped tool factories (read-only, Layer 1) ──────────


def _make_read_file(root: Path) -> Any:
    """Return a read_project_file impl constrained to ``root``."""

    async def _impl(inp: dict[str, Any]) -> str:
        relative = inp.get("path", "")
        if not relative:
            return "Error: path is required"

        safe_path = _safe_path_under(root, relative)
        if safe_path is None:
            return f"Error: path '{relative}' is outside the project root — access denied"

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

    return _impl


def _make_list_dir(root: Path) -> Any:
    """Return a list_project_dir impl constrained to ``root``."""

    max_entries = 500

    async def _impl(inp: dict[str, Any]) -> str:
        relative = inp.get("path", ".")
        safe_path = _safe_path_under(root, relative)
        if safe_path is None:
            return f"Error: path '{relative}' is outside the project root — access denied"

        if not safe_path.exists():
            return f"Error: '{relative}' does not exist"
        if not safe_path.is_dir():
            return f"Error: '{relative}' is not a directory"

        try:
            entries = list(safe_path.iterdir())
            dirs = sorted(e.name for e in entries if e.is_dir())
            files = sorted(e.name for e in entries if not e.is_dir())
            names = [n + "/" for n in dirs] + files
            truncated = False
            if len(names) > max_entries:
                names = names[:max_entries]
                truncated = True
            result = "\n".join(names)
            if truncated:
                result += f"\n[...truncated at {max_entries} entries]"
            return result if result else "(empty directory)"
        except Exception as exc:
            return f"List dir failed: {exc}"

    return _impl


def _make_git_status(root: Path) -> Any:
    """Return a git_status impl that runs ``git status`` in ``root``."""

    async def _impl(inp: dict[str, Any]) -> str:  # noqa: ARG001
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--branch",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return "Error: git status timed out after 10s"

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return f"Error: git status exited {proc.returncode}: {err}"

            return stdout.decode("utf-8", errors="replace")
        except Exception as exc:
            return f"git status failed: {exc}"

    return _impl


def _make_git_diff(root: Path) -> Any:
    """Return a git_diff impl that runs ``git diff`` in ``root``."""

    max_chars = 16000

    async def _impl(inp: dict[str, Any]) -> str:
        path_arg = inp.get("path", "")
        cmd = ["git", "-C", str(root), "diff"]

        if path_arg:
            safe_path = _safe_path_under(root, path_arg)
            if safe_path is None:
                return f"Error: path '{path_arg}' is outside the project root — access denied"
            cmd.append(str(safe_path))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                return "Error: git diff timed out after 10s"

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return f"Error: git diff exited {proc.returncode}: {err}"

            output = stdout.decode("utf-8", errors="replace")
            if len(output) > max_chars:
                output = output[:max_chars] + f"\n[...truncated at {max_chars} chars]"
            return output if output else "(no diff — working tree is clean)"
        except Exception as exc:
            return f"git diff failed: {exc}"

    return _impl


# ── Forge / Ares project-scoped Tool definitions ──────────────────────────────

READ_PROJECT_FILE = Tool(
    name="read_project_file",
    description=(
        "Read a file within the current Forge project root. "
        "Path must be relative to the project root. "
        "Access is constrained to that root — no path traversal. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path from the project root",
            },
            "max_chars": {
                "type": "integer",
                "description": "Max characters to return",
                "default": 8000,
            },
        },
        "required": ["path"],
    },
)

LIST_PROJECT_DIR = Tool(
    name="list_project_dir",
    description=(
        "List the contents of a directory within the current Forge project root. "
        "Returns names with trailing '/' for subdirectories, sorted dirs-first. "
        "Defaults to the project root ('.') when no path is given. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative path from the project root (default '.')",
                "default": ".",
            },
        },
        "required": [],
    },
)

GIT_STATUS = Tool(
    name="git_status",
    description=(
        "Run 'git status --porcelain=v1 --branch' in the current Forge project root "
        "and return the output. Read-only. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)

GIT_DIFF = Tool(
    name="git_diff",
    description=(
        "Run 'git diff' in the current Forge project root and return the output. "
        "Optionally scoped to a single file (path relative to project root). "
        "Output is capped at 16000 characters. Read-only. [layer:1]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Optional relative path to scope the diff to a single file",
            },
        },
        "required": [],
    },
)


def register_ares_coding_tools(
    registry: AuthorizedToolRegistry,
    project_path: str,
) -> None:
    """Register the four read-only Forge coding tools into ``registry``.

    All tools are constrained to ``project_path`` (resolved to an absolute
    path at registration time).  Layer 1 — read-only, no side-effects.
    """
    root = Path(project_path).resolve()
    registry.register(READ_PROJECT_FILE, _make_read_file(root), layer=1)
    registry.register(LIST_PROJECT_DIR, _make_list_dir(root), layer=1)
    registry.register(GIT_STATUS, _make_git_status(root), layer=1)
    registry.register(GIT_DIFF, _make_git_diff(root), layer=1)


# ── Tool implementations ──────────────────────────────────────────────────────


def _make_query_memory(agent_id: str | None) -> Any:
    """Return a ``_query_memory`` implementation gated to ``agent_id``'s allowance.

    M3 FAIL-CLOSED: the agent's allowance is resolved once at registration time
    from ``agent_id``.  The requested ``scope`` from tool input is validated
    against that allowance before any DB query.  Scopes outside the allowance are
    DROPPED; if nothing remains, the function returns "No relevant memory found."
    without ever touching the database.

    If ``agent_id`` is None/empty/unknown, ``allowed_scopes_for_agent`` returns a
    denied allowance and every scope request returns empty (fail-closed default).
    """
    from artemis.floating_artemis.memory import _enforce_agent_scope_set
    from artemis.identity.scope_policy import allowed_scopes_for_agent

    # Resolve the allowance once — fail-closed for None/unknown agent_id.
    _agent_id: str = agent_id or ""
    _allowance = allowed_scopes_for_agent(_agent_id) if _agent_id else None

    async def _query_memory_impl(inp: dict[str, Any]) -> str:
        """Query the Artemis memory store — scope-gated to the calling agent."""
        query = inp.get("query", "")
        scope = inp.get("scope", "global:global")
        limit = int(inp.get("limit", 10))
        if scope == "all":
            scope = "global:global"

        try:
            import artemis.db as _db
            from artemis.memory.retrieval import search_observations
            from artemis.memory.schemas import Scope

            scope_kind, scope_id = scope.split(":") if ":" in scope else (scope, "default")
            requested_scope = Scope(scope_kind=scope_kind, scope_id=scope_id)

            # M3: enforce agent allowance — drop any scope not permitted.
            enforced = _enforce_agent_scope_set(_agent_id, [requested_scope])
            if not enforced:
                logger.info(
                    "query_memory: scope %s:%s denied for agent_id=%r — returning empty",
                    scope_kind,
                    scope_id,
                    _agent_id,
                )
                return "No relevant memory found."

            async with _db.SessionLocal() as session:
                results = await search_observations(
                    session,
                    scope_set=enforced,
                    query=query,
                    limit=limit,
                )
            if not results:
                return "No relevant memory found."
            lines = [f"[{r.scope_kind}:{r.scope_id}] {r.content}" for r in results]
            return "\n".join(lines)
        except Exception as exc:
            return f"Memory query failed: {exc}"

    return _query_memory_impl


# Keep a bare (ungated) reference so callers that genuinely need no gating
# (e.g. tests of the raw function shape) still work, but it is NOT registered
# in any live tool registry — only _make_query_memory(agent_id) is.
async def _query_memory(inp: dict[str, Any]) -> str:
    """Ungated fallback — DO NOT register in production; use _make_query_memory."""
    return await _make_query_memory(None)(inp)


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


# Brief-suppression scope: must match what sources._safe_brief_exclusions queries.
_BRIEF_EXCLUSION_SCOPE = "agent:floating-artemis"
_BRIEF_EXCLUSION_PREFIX = "brief_exclusion:"


async def _set_brief_exclusion(inp: dict[str, Any]) -> str:
    """Mute a Jira ticket from the morning brief.

    Writes a tagged memory observation so the brief can filter it out.
    The agent calls this when Jon says something like "stop surfacing MT-456
    — that's long-term".  Confirm conversationally after writing; no buttons.
    """
    ticket_key = (inp.get("ticket_key") or "").strip().upper()
    reason = (inp.get("reason") or "").strip()
    if not ticket_key:
        return "Error: ticket_key is required (e.g. 'MT-456')"

    content = f"{_BRIEF_EXCLUSION_PREFIX}{ticket_key}"
    if reason:
        content += f" reason={reason}"
    result = await _write_memory(
        {"content": content, "scope": _BRIEF_EXCLUSION_SCOPE, "source": "operator"}
    )
    if "failed" in result.lower():
        return result
    return (
        f"Got it — {ticket_key} will no longer appear in your morning brief. "
        f"Say 'unmute {ticket_key}' any time to bring it back."
    )


async def _clear_brief_exclusion(inp: dict[str, Any]) -> str:
    """Unmute a Jira ticket so it reappears in the morning brief.

    Writes a clear-marker observation that overrides the suppression.
    The agent calls this when Jon says "unmute MT-456" or "show MT-456 again".
    """
    ticket_key = (inp.get("ticket_key") or "").strip().upper()
    if not ticket_key:
        return "Error: ticket_key is required (e.g. 'MT-456')"

    content = f"{_BRIEF_EXCLUSION_PREFIX}{ticket_key} cleared"
    result = await _write_memory(
        {"content": content, "scope": _BRIEF_EXCLUSION_SCOPE, "source": "operator"}
    )
    if "failed" in result.lower():
        return result
    return (
        f"Done — {ticket_key} will show up in your morning brief again starting tomorrow."
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

SET_BRIEF_EXCLUSION = Tool(
    name="set_brief_exclusion",
    description=(
        "Mute a Jira ticket from the morning brief. "
        "Call when Jon says things like 'stop surfacing MT-456', 'those are long-term', "
        "'don't show me MT-456 any more'. Writes a suppression to memory. "
        "Confirm conversationally — no buttons. [layer:2]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticket_key": {
                "type": "string",
                "description": "Jira ticket key to suppress, e.g. 'MT-456'",
            },
            "reason": {
                "type": "string",
                "description": "Optional short reason (stored for context)",
            },
        },
        "required": ["ticket_key"],
    },
)

CLEAR_BRIEF_EXCLUSION = Tool(
    name="clear_brief_exclusion",
    description=(
        "Unmute a previously suppressed Jira ticket so it reappears in the morning brief. "
        "Call when Jon says 'unmute MT-456', 'show MT-456 again', etc. "
        "Confirm conversationally. [layer:2]"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticket_key": {
                "type": "string",
                "description": "Jira ticket key to un-suppress, e.g. 'MT-456'",
            },
        },
        "required": ["ticket_key"],
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


def register_core_tools(registry: AuthorizedToolRegistry, agent_id: str | None = None) -> None:
    """Register all core tools into the provided registry.

    ``agent_id`` MUST be supplied for any live session so that ``query_memory``
    is gated to that agent's scope allowance (M3).  When ``agent_id`` is None
    or unknown the tool returns empty for every request (fail-closed).
    """
    # M3: gate query_memory to the calling agent's allowance.
    gated_query_memory = _make_query_memory(agent_id)
    registry.register(QUERY_MEMORY, gated_query_memory, layer=1)
    registry.register(WRITE_MEMORY, _write_memory, layer=2)
    registry.register(LIST_SCOPES, _list_scopes, layer=1)
    registry.register(SURFACE_STATUS, _surface_status, layer=1)
    registry.register(LIST_ROUTES, _list_routes, layer=1)
    registry.register(READ_FILE, _read_file, layer=1)
    registry.register(PROPOSE_EDIT, _propose_edit, layer=2)
    registry.register(SET_PREF, _set_pref, layer=2)
    registry.register(SET_BRIEF_EXCLUSION, _set_brief_exclusion, layer=2)
    registry.register(CLEAR_BRIEF_EXCLUSION, _clear_brief_exclusion, layer=2)
    registry.register(SPAWN_SUBAGENT, _spawn_subagent, layer=3)
