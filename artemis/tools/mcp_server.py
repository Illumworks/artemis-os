"""Artemis Tools MCP Server (stream CC1).

Subscription-compatible tool-execution path for the ``claude-code`` provider.
``claude -p --mcp-config`` spawns this module as a stdio subprocess; it exposes
exactly one agent's declared tools (``agent.tools``), reusing the verbatim P2/P3
in-process tool registry (``artemis.tools.registry``) as a thin serving layer.

Invocation (the INTERFACE CONTRACT — CC2 depends on this; do not drift):

    python -m artemis.tools.mcp_server \\
        --agent-id <dotted_id> --run-id <uuid> [--pipeline-run-id <id>]

- Transport: stdio.
- MCP server name: ``artemis``.
- Tool naming: artemis ``signal_queue.write`` → MCP ``signal_queue_write``
  (claude-code sees ``mcp__artemis__signal_queue_write``).
- Scoping: ONLY ``agent.tools`` ∩ known registry are exposed.
- Per-write commit: after every successful tool call ``session.commit()`` runs.
  A read or a string-returning no-write (``VALIDATION_ERROR`` / ``PERMISSION_DENIED``
  / ``STUB:``) commits a no-op; a handler exception rolls back and returns an
  error string — the server never crashes mid-session.
- Lifetime: runs until stdin closes (claude-code manages it); clean shutdown
  closes the DB session.

MCP SDK: the low-level ``mcp.server.Server`` + ``mcp.server.stdio.stdio_server``
(mcp 1.27.1). The low-level API is used (not FastMCP) because it accepts each
tool's raw JSON-schema ``inputSchema`` dict verbatim from the artemis
``Tool.input_schema`` — no schema re-derivation.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from sqlalchemy.ext.asyncio import AsyncSession

# Register every ORM model whose table is an FK target of a tool write, so
# SQLAlchemy's mapper is fully configured in this standalone process. Without
# this, signal_queue.write fails at flush — SignalQueue.pipeline_run_id FKs
# pipeline_runs, whose Table is otherwise never imported into Base.metadata
# here. The main app imports all models at startup; this entrypoint must too.
import artemis.marketing.models  # noqa: F401
import artemis.pipelines.models  # noqa: F401
import artemis.tools  # noqa: F401 — import side-effect registers all tool factories
from artemis.agent.types import Tool, ToolImpl
from artemis.builders import repository as repo
from artemis.db import SessionLocal
from artemis.tools.context import ToolContext
from artemis.tools.models import ToolInvocation
from artemis.tools.registry import get_factory, known_tool_names

logger = logging.getLogger(__name__)

#: The MCP server name. claude-code prefixes tools as ``mcp__artemis__<tool>``.
SERVER_NAME = "artemis"


# ── Tool-name mapping (the INTERFACE CONTRACT — CC2 builds --allowedTools from it) ──


def mcp_tool_name(artemis_name: str) -> str:
    """Map an artemis tool name to its MCP tool name (dots → underscores).

    ``signal_queue.write`` → ``signal_queue_write``. Artemis names contain
    exactly one dot, so this is unambiguous.
    """
    return artemis_name.replace(".", "_")


def artemis_tool_name(mcp_name: str) -> str:
    """Inverse of :func:`mcp_tool_name`.

    Rule: artemis tool names already contain underscores inside their segments
    (``signal_queue``), so a blind "swap the last underscore for a dot" is wrong.
    Instead we resolve against the *known registry*: build the forward map
    ``{mcp_tool_name(n): n for n in known_tool_names()}`` and look ``mcp_name`` up.
    This is deterministic (no collisions — every artemis name has exactly one dot)
    and robust to underscores in segments. Raises ``KeyError`` for an unknown
    MCP name so callers can't silently misroute.
    """
    forward = {mcp_tool_name(n): n for n in known_tool_names()}
    return forward[mcp_name]


# ── Tool-invocation logging (CC17) ────────────────────────────────────────────

#: Result prefixes that indicate a logical failure (not a hard exception).
_FAILURE_PREFIXES = (
    "VALIDATION_ERROR",
    "PERMISSION_DENIED",
    "STUB:",
    "TOOL_ERROR:",
    "UNKNOWN_TOOL:",
)


def _summarize_args(args: dict[str, object], max_len: int = 500) -> str:
    """Return a ≤max_len string representation of call arguments."""
    try:
        raw = json.dumps(args, default=str)
    except Exception:
        raw = repr(args)
    return raw[:max_len] if len(raw) > max_len else raw


async def _log_invocation(
    session: AsyncSession,
    agent_run_id: str,
    pipeline_run_id: str | None,
    tool_name: str,
    args_summary: str | None,
    result_preview: str | None,
    success: bool,
) -> None:
    """Insert one ToolInvocation row and commit it independently.

    Each invocation is its own committed fact — the MCP server's session
    commits once per tool call so provenance is durable even if the agent
    run subsequently fails.
    """
    row = ToolInvocation(
        agent_run_id=agent_run_id,
        pipeline_run_id=pipeline_run_id,
        tool_name=tool_name,
        args_summary=args_summary,
        result_preview=result_preview,
        success=success,
    )
    session.add(row)
    await session.commit()


# ── Core, testable tool-set construction ───────────────────────────────────────


async def build_tool_set(
    session: AsyncSession,
    agent_id: str,
    run_id: str,
    pipeline_run_id: str | None = None,
) -> dict[str, tuple[Tool, ToolImpl]]:
    """Build the scoped MCP tool set for one agent run.

    Loads the ``Agent`` row, intersects ``agent.tools`` with the known registry,
    instantiates each factory with a bound :class:`ToolContext`, and returns a
    dict keyed by *MCP* tool name → ``(Tool, ToolImpl)``. Unknown tool names in
    ``agent.tools`` are skipped (logged to stderr), never raised.

    Raises ``ValueError`` only if the agent itself is not found.
    """
    agent = await repo.get_agent(session, agent_id)  # raises ValueError if missing

    ctx = ToolContext(
        session=session,
        agent_id=agent.agent_id,
        agent_db_id=agent.id,
        agent_run_id=run_id,
        pipeline_run_id=pipeline_run_id,
    )

    declared = agent.tools or []
    tool_set: dict[str, tuple[Tool, ToolImpl]] = {}
    for entry in declared:
        name = entry if isinstance(entry, str) else str(entry.get("name", ""))
        factory = get_factory(name)
        if factory is None:
            logger.warning(
                "agent %r declares unknown tool %r — skipping (known: %s)",
                agent_id,
                name,
                known_tool_names(),
            )
            continue
        tool_def, impl = factory(ctx)
        tool_set[mcp_tool_name(name)] = (tool_def, impl)
    return tool_set


def _build_server(
    session: AsyncSession,
    tool_set: dict[str, tuple[Tool, ToolImpl]],
    run_id: str,
    pipeline_run_id: str | None = None,
) -> Server:
    """Wire a low-level MCP ``Server`` over a pre-built tool set.

    The ``list_tools`` handler advertises each tool with its verbatim
    ``input_schema``. The ``call_tool`` handler dispatches to the artemis
    ``ToolImpl``, commits on success, rolls back on exception, and always
    returns text content (never crashes the session).

    CC17: every tool invocation is logged to ``tool_invocations`` via
    ``_log_invocation`` — one committed row per call, regardless of outcome.
    The artemis-style tool name (e.g. ``signal_queue.write``) is resolved
    from the MCP name via ``artemis_tool_name``; unknown names fall back to
    the MCP name so logs are never silently lost.
    """
    server: Server = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name=mcp_name,
                description=tool_def.description,
                inputSchema=tool_def.input_schema,
            )
            for mcp_name, (tool_def, _impl) in sorted(tool_set.items())
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, object]) -> list[mcp_types.TextContent]:
        # Resolve artemis-style name for logging (signal_queue.write, not signal_queue_write).
        try:
            a_name = artemis_tool_name(name)
        except KeyError:
            a_name = name  # unknown MCP name — log as-is

        args_summary = _summarize_args(dict(arguments))

        entry = tool_set.get(name)
        if entry is None:
            result_text = f"UNKNOWN_TOOL: {name}"
            await _log_invocation(
                session, run_id, pipeline_run_id, a_name, args_summary, result_text[:500], False
            )
            return [mcp_types.TextContent(type="text", text=result_text)]

        _tool_def, impl = entry
        try:
            result = await impl(dict(arguments))
        except Exception as exc:  # never crash the session mid-run
            await session.rollback()
            logger.exception("tool %r raised; rolled back", name)
            error_preview = f"EXCEPTION: {exc!s}"[:500]
            await _log_invocation(
                session, run_id, pipeline_run_id, a_name, args_summary, error_preview, False
            )
            return [mcp_types.TextContent(type="text", text=f"TOOL_ERROR: {exc}")]

        # Determine success: False for known failure-prefix results.
        success = not any(result.startswith(p) for p in _FAILURE_PREFIXES)
        result_preview = result[:500] if isinstance(result, str) else None

        # Log before the per-write commit so the log row is always committed
        # even if session.commit() below is a no-op (read-only tool).
        await _log_invocation(
            session, run_id, pipeline_run_id, a_name, args_summary, result_preview, success
        )

        # Per-write commit: a successful read / no-write commits a no-op.
        # Note: _log_invocation already committed the session; this second
        # commit is a no-op for tools that did no additional writes, and a
        # flush for tools that wrote signal_queue rows etc. in the same call.
        await session.commit()
        return [mcp_types.TextContent(type="text", text=result)]

    return server


# ── stdio entrypoint ────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m artemis.tools.mcp_server")
    parser.add_argument("--agent-id", required=True, help="Dotted agent id to bind to.")
    parser.add_argument("--run-id", required=True, help="Agent run UUID (for provenance).")
    parser.add_argument("--pipeline-run-id", default=None, help="Optional pipeline run id.")
    return parser.parse_args(argv)


async def _serve(agent_id: str, run_id: str, pipeline_run_id: str | None) -> int:
    """Open a session, build the scoped tool set, and serve over stdio.

    Returns a process exit code (0 on clean shutdown, non-zero on fatal setup
    error such as an unknown agent).
    """
    async with SessionLocal() as session:
        try:
            tool_set = await build_tool_set(session, agent_id, run_id, pipeline_run_id)
        except ValueError as exc:
            print(f"FATAL: {exc}", file=sys.stderr, flush=True)
            return 2

        logger.info(
            "artemis MCP server bound to agent=%s run=%s tools=%s",
            agent_id,
            run_id,
            sorted(tool_set),
        )
        server = _build_server(session, tool_set, run_id, pipeline_run_id)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    args = _parse_args(argv)
    import anyio

    return anyio.run(
        _serve,
        args.agent_id,
        args.run_id,
        args.pipeline_run_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
