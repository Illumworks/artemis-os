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
import logging
import sys

import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from sqlalchemy.ext.asyncio import AsyncSession

import artemis.tools  # noqa: F401 — import side-effect registers all tool factories
from artemis.agent.types import Tool, ToolImpl
from artemis.builders import repository as repo
from artemis.db import SessionLocal
from artemis.tools.context import ToolContext
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


def _build_server(session: AsyncSession, tool_set: dict[str, tuple[Tool, ToolImpl]]) -> Server:
    """Wire a low-level MCP ``Server`` over a pre-built tool set.

    The ``list_tools`` handler advertises each tool with its verbatim
    ``input_schema``. The ``call_tool`` handler dispatches to the artemis
    ``ToolImpl``, commits on success, rolls back on exception, and always
    returns text content (never crashes the session).
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
        entry = tool_set.get(name)
        if entry is None:
            return [mcp_types.TextContent(type="text", text=f"UNKNOWN_TOOL: {name}")]
        _tool_def, impl = entry
        try:
            result = await impl(dict(arguments))
        except Exception as exc:  # never crash the session mid-run
            await session.rollback()
            logger.exception("tool %r raised; rolled back", name)
            return [mcp_types.TextContent(type="text", text=f"TOOL_ERROR: {exc}")]
        # Per-write commit: a successful read / no-write commits a no-op.
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
        server = _build_server(session, tool_set)
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
