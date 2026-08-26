"""Artemis Tools MCP Server (stream CC1, extended CC19, CC20).

Subscription-compatible tool-execution path for the ``claude-code`` provider.
``claude -p --mcp-config`` spawns this module as a stdio subprocess; it exposes
exactly one agent's declared tools (``agent.tools``), reusing the verbatim P2/P3
in-process tool registry (``artemis.tools.registry``) as a thin serving layer.

Invocation (the INTERFACE CONTRACT — CC2 depends on this; do not drift):

    python -m artemis.tools.mcp_server \\
        --agent-id <dotted_id> --run-id <uuid> [--pipeline-run-id <id>]

CC19 adds a second mode — Builder-scoped tool execution:

    python -m artemis.tools.mcp_server \\
        --builder-session-id <id>

In this mode the server exposes nine Builder-scoped tools (five from CC19 +
three grounding tools from CC20 + one memory tool from M2):
  builder_read_existing, builder_read_capabilities, builder_read_recent_runs,
  builder_propose, builder_test_run,
  builder_read_tool_signatures, builder_read_db_schema, builder_read_skill_catalog,
  builder_search_memory.

The ``builder_session_id`` scope is established via a contextvar
(``artemis.builder.context.builder_session_id_var``) BEFORE the subprocess is
launched; the MCP server reads it at tool-call time.  The agent-run tools
(``--agent-id``) and builder tools (``--builder-session-id``) are mutually
exclusive; pass exactly one scope.

Floating Artemis adds a third mode — session-scoped auto-invoke tool execution:

    python -m artemis.tools.mcp_server \\
        --floating-session-id <session_id> [--tool-name <name> ...]

In this mode the server reconstructs Floating Artemis's tool registry for the
current app surfaces, filters it to the exact ``--tool-name`` allowlist from
the parent turn handler, and serves only layer-1/2 tools that are safe to run
without the in-process confirmation yield.

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
from artemis.floating_artemis.tool_registry import build_authorized_tool_registry
from artemis.routes.status import get_status
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
    *,
    agent_run_id: str | None = None,
    builder_session_id: int | None = None,
    pipeline_run_id: str | None = None,
    tool_name: str,
    args_summary: str | None,
    result_preview: str | None,
    success: bool,
) -> None:
    """Insert one ToolInvocation row and commit it independently.

    Each invocation is its own committed fact — the MCP server's session
    commits once per tool call so provenance is durable even if the agent
    run subsequently fails.

    CC21: exactly one of agent_run_id / builder_session_id must be set
    (enforced by the ck_tool_invocations_scope CHECK constraint).  Pipeline
    callers pass agent_run_id; Builder callers pass builder_session_id.
    """
    row = ToolInvocation(
        agent_run_id=agent_run_id,
        builder_session_id=builder_session_id,
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
                session,
                agent_run_id=run_id,
                pipeline_run_id=pipeline_run_id,
                tool_name=a_name,
                args_summary=args_summary,
                result_preview=result_text[:500],
                success=False,
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
                session,
                agent_run_id=run_id,
                pipeline_run_id=pipeline_run_id,
                tool_name=a_name,
                args_summary=args_summary,
                result_preview=error_preview,
                success=False,
            )
            return [mcp_types.TextContent(type="text", text=f"TOOL_ERROR: {exc}")]

        # Determine success: False for known failure-prefix results.
        success = not any(result.startswith(p) for p in _FAILURE_PREFIXES)
        result_preview = result[:500] if isinstance(result, str) else None

        # Log before the per-write commit so the log row is always committed
        # even if session.commit() below is a no-op (read-only tool).
        await _log_invocation(
            session,
            agent_run_id=run_id,
            pipeline_run_id=pipeline_run_id,
            tool_name=a_name,
            args_summary=args_summary,
            result_preview=result_preview,
            success=success,
        )

        # Per-write commit: a successful read / no-write commits a no-op.
        # Note: _log_invocation already committed the session; this second
        # commit is a no-op for tools that did no additional writes, and a
        # flush for tools that wrote signal_queue rows etc. in the same call.
        await session.commit()
        return [mcp_types.TextContent(type="text", text=result)]

    return server


async def build_floating_artemis_tool_set(
    tool_names: set[str] | None = None,
    agent_id: str | None = None,
    speaker_id: str | None = None,
) -> dict[str, tuple[Tool, ToolImpl]]:
    """Build the Floating Artemis auto-invoke tool set for MCP serving.

    The subprocess reconstructs the current Floating Artemis registry from app
    surfaces, then filters to the exact allowlist chosen by the parent turn
    handler. Only layer-1/2 tools are exposed on this path because claude-code's
    subprocess loop cannot yield back into Floating Artemis's in-process
    confirmation flow for layer-3/4 tools.

    M3: ``agent_id`` is threaded into ``build_authorized_tool_registry`` so
    ``query_memory`` is gated to the calling agent's scope allowance.  When
    ``agent_id`` is None the tool fails closed (empty results) for every request.
    """
    try:
        status = await get_status()
        surfaces_raw = status.get("available_surfaces", []) if isinstance(status, dict) else []
        available_surfaces = set(surfaces_raw if isinstance(surfaces_raw, list) else [])
    except Exception:
        logger.debug("floating MCP could not load status surfaces; falling back to empty set")
        available_surfaces = set()

    registry = build_authorized_tool_registry(
        available_surfaces, agent_id=agent_id, speaker_id=speaker_id
    )
    wanted = tool_names or set()

    tool_set: dict[str, tuple[Tool, ToolImpl]] = {}
    for entry in registry.all_entries():
        if entry.layer > 2:
            continue
        if wanted and entry.tool.name not in wanted:
            continue
        tool_set[mcp_tool_name(entry.tool.name)] = (entry.tool, entry.impl)
    return tool_set


def _build_floating_artemis_server(
    session: AsyncSession,
    tool_set: dict[str, tuple[Tool, ToolImpl]],
) -> Server:
    """Wire a low-level MCP server for Floating Artemis auto-invoke tools."""
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
        except Exception as exc:
            await session.rollback()
            logger.exception("floating_artemis tool %r raised; rolled back", name)
            return [mcp_types.TextContent(type="text", text=f"TOOL_ERROR: {exc}")]

        await session.commit()
        return [mcp_types.TextContent(type="text", text=result)]

    return server


# ── Builder-scoped MCP tools (CC19) ───────────────────────────────────────────

#: Per-session set of run PKs returned by builder_read_recent_runs.
#: Keyed by builder_session_id (int). Used to validate citations in
#: builder_propose — same protection as the in-process _propose closure.
_builder_seen_run_ids: dict[int, set[int]] = {}

#: MCP tool names for the Builder scope (cc19 + cc20 + m2).
BUILDER_MCP_TOOL_NAMES: tuple[str, ...] = (
    "builder_read_existing",
    "builder_read_capabilities",
    "builder_read_recent_runs",
    "builder_propose",
    "builder_test_run",
    # CC20 grounding tools.
    "builder_read_tool_signatures",
    "builder_read_db_schema",
    "builder_read_skill_catalog",
    # M2 memory tool.
    "builder_search_memory",
)

_BUILDER_TOOL_SCHEMAS: list[mcp_types.Tool] = [
    mcp_types.Tool(
        name="builder_read_existing",
        description="List existing definitions of a given kind (agent/skill/workflow).",
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["agent", "skill", "workflow"],
                    "description": "The kind of definition to list.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of results (default 50).",
                    "default": 50,
                },
            },
            "required": ["kind"],
        },
    ),
    mcp_types.Tool(
        name="builder_read_capabilities",
        description="Return available providers, models, and integrations.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    mcp_types.Tool(
        name="builder_read_recent_runs",
        description=(
            "Return the most recent agent runs + trajectory summaries. "
            "Use in edit sessions to surface self-improvement context."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent_id string of the agent to look up.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max runs to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["agent_id"],
        },
    ),
    mcp_types.Tool(
        name="builder_propose",
        description=(
            "Stage a draft definition as a DefinitionProposal awaiting user approval. "
            "Use for both agent definitions and co-proposed skills."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["agent", "skill", "workflow", "automation"],
                    "description": "Kind of definition being proposed.",
                },
                "definition": {
                    "type": "object",
                    "description": "The draft definition object.",
                },
                "target_id": {
                    "type": "integer",
                    "description": "Non-null when revising an existing definition.",
                },
                "citations": {
                    "type": "object",
                    "description": (
                        "Self-improvement citations. Shape: "
                        "{run_ids: [int, ...], observations: [...], summary: str}. "
                        "run_ids MUST be IDs returned by builder_read_recent_runs "
                        "in this session — never fabricate or guess IDs."
                    ),
                },
            },
            "required": ["kind", "definition"],
        },
    ),
    mcp_types.Tool(
        name="builder_test_run",
        description=(
            "Fire a sandboxed trial run of the current draft definition against a test prompt. "
            "Read-only tools only. Returns output + tools_skipped list. "
            "Note: when claude-code is the active adapter, this uses a fallback provider "
            "(anthropic, openai, etc.) for sandbox execution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "definition": {
                    "type": "object",
                    "description": "The draft agent definition to test.",
                },
                "prompt": {
                    "type": "string",
                    "description": "The test prompt to run against the draft.",
                },
            },
            "required": ["definition", "prompt"],
        },
    ),
    # ── CC20 grounding tools ────────────────────────────────────────────────
    mcp_types.Tool(
        name="builder_read_tool_signatures",
        description=(
            "Return the actual parameter schemas + valid enum values for all tools "
            "an agent has access to. Grounds the Builder against real allowed_status_values "
            "so it never enumerates hallucinated status names. "
            "MUST be called after read_recent_runs() and BEFORE propose()."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent_id string (dotted slug) of the agent to inspect.",
                },
            },
            "required": ["agent_id"],
        },
    ),
    mcp_types.Tool(
        name="builder_read_db_schema",
        description=(
            "Return the actual DB schema (columns, CHECK constraints, FK relationships, "
            "unique constraints) for the requested tables. "
            "Use before proposing changes that reference DB column names or status values."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Table names to inspect (e.g. ['signal_queue', 'definition_proposals']).",
                },
            },
            "required": ["table_names"],
        },
    ),
    mcp_types.Tool(
        name="builder_read_skill_catalog",
        description=(
            "List ALL registered tools across the platform plus all skills table rows. "
            "Use before co-proposing a skill to confirm the name is not already taken."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Optional filter: 'tool' or 'skill' (default: both).",
                },
            },
            "required": [],
        },
    ),
    # ── M2 memory tool ──────────────────────────────────────────────────────────
    mcp_types.Tool(
        name="builder_search_memory",
        description=(
            "Retrieve curated memory observations for an agent across its full run history. "
            "Returns observations ranked by recency, relevance, and evidence chain quality. "
            "MUST be called after read_recent_runs() and BEFORE propose() in edit sessions. "
            "Observations are more authoritative than trajectory summaries — they have "
            "evidence chains and are deduplicated across all runs."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "The agent_id string (dotted slug) to search memory for.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional natural-language query to focus retrieval.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max observations to return (default 10, max 50).",
                    "default": 10,
                },
            },
            "required": ["agent_id"],
        },
    ),
]


def _build_builder_server(session: AsyncSession, builder_session_id: int) -> Server:
    """Wire a low-level MCP Server with the five Builder-scoped tools (CC19).

    Tool calls are dispatched to the builder engine primitives, scoped to the
    given builder_session_id.  Citations validation uses a per-session
    _seen_run_ids set so builder_propose cannot fabricate run IDs.

    test_run recursion concern: when claude-code is the active adapter (i.e.,
    this server IS the MCP subprocess launched by ClaudeCodeAdapter), we walk
    the provider cascade EXCLUDING claude-code for sandbox execution.  If no
    other provider is available, we return an explicit error rather than
    recursing into another claude-code subprocess.
    """
    # Per-session seen-run-ids tracking (mutable, captured by closure).
    if builder_session_id not in _builder_seen_run_ids:
        _builder_seen_run_ids[builder_session_id] = set()
    seen_run_ids = _builder_seen_run_ids[builder_session_id]

    server: Server = Server(SERVER_NAME)

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def _list_tools() -> list[mcp_types.Tool]:
        return list(_BUILDER_TOOL_SCHEMAS)

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def _call_tool(name: str, arguments: dict[str, object]) -> list[mcp_types.TextContent]:
        args = dict(arguments)
        args_summary = _summarize_args(args)
        try:
            result = await _dispatch_builder_tool(
                name=name,
                args=args,
                session=session,
                builder_session_id=builder_session_id,
                seen_run_ids=seen_run_ids,
            )
        except Exception as exc:
            await session.rollback()
            logger.exception("builder tool %r raised; rolled back", name)
            error_preview = f"EXCEPTION: {exc!s}"[:500]
            await _log_invocation(
                session,
                builder_session_id=builder_session_id,
                tool_name=name,
                args_summary=args_summary,
                result_preview=error_preview,
                success=False,
            )
            return [mcp_types.TextContent(type="text", text=f"TOOL_ERROR: {exc}")]

        # Determine success: False for known failure-prefix results.
        success = not any(result.startswith(p) for p in _FAILURE_PREFIXES)
        result_preview = result[:500] if isinstance(result, str) else None
        # CC21: log Builder tool calls with builder_session_id scope.
        await _log_invocation(
            session,
            builder_session_id=builder_session_id,
            tool_name=name,
            args_summary=args_summary,
            result_preview=result_preview,
            success=success,
        )
        await session.commit()
        return [mcp_types.TextContent(type="text", text=result)]

    return server


async def _dispatch_builder_tool(
    *,
    name: str,
    args: dict[str, object],
    session: AsyncSession,
    builder_session_id: int,
    seen_run_ids: set[int],
) -> str:
    """Dispatch a builder MCP tool call to the appropriate engine primitive."""
    import json as _json
    from typing import Any as _Any

    from artemis.builder import engine as builder_engine

    # Narrow argument dict to Any for convenience — the MCP protocol delivers
    # JSON-decoded values so these casts are safe at runtime.
    _args: dict[str, _Any] = args

    if name == "builder_read_existing":
        results = await builder_engine.read_existing(
            str(_args.get("kind", "agent")),
            db_session=session,
            limit=int(_args.get("limit", 50)),
        )
        return _json.dumps(results, indent=2)

    if name == "builder_read_capabilities":
        result = await builder_engine.read_capabilities(db_session=session)
        return _json.dumps(result, indent=2)

    if name == "builder_read_recent_runs":
        runs = await builder_engine.read_recent_runs(
            str(_args.get("agent_id", "")),
            db_session=session,
            limit=int(_args.get("limit", 10)),
        )
        # Record returned PKs for citation validation.
        for entry in runs:
            if "id" in entry:
                seen_run_ids.add(int(entry["id"]))
        return _json.dumps(runs, indent=2)

    if name == "builder_propose":
        citations = _args.get("citations")
        if citations and isinstance(citations, dict):
            cited_ids = [int(r) for r in citations.get("run_ids", [])]
            bad_ids = [rid for rid in cited_ids if rid not in seen_run_ids]
            if bad_ids:
                return _json.dumps(
                    {
                        "error": (
                            "run_ids validation failed: the following IDs were not returned by "
                            f"builder_read_recent_runs in this session and cannot be cited: "
                            f"{bad_ids}. Only reference run IDs from the set "
                            "builder_read_recent_runs returned."
                        )
                    }
                )

        definition = _args.get("definition", {})
        if not isinstance(definition, dict):
            definition = {}
        target_id_raw = _args.get("target_id")
        target_id = int(target_id_raw) if target_id_raw is not None else None

        proposal_id = await builder_engine.propose(
            str(_args.get("kind", "agent")),
            definition,
            db_session=session,
            builder_session_id=builder_session_id,
            target_id=target_id,
            proposed_by="builder",
            citations=citations if isinstance(citations, dict) else None,
        )
        # Commit is handled by the outer _call_tool wrapper.
        return _json.dumps({"proposal_id": proposal_id, "status": "pending"})

    if name == "builder_test_run":
        # Recursion concern: this MCP server is itself running as a subprocess
        # launched by ClaudeCodeAdapter._complete_with_tools().  We must NOT
        # use claude-code again for sandbox execution — that would recurse.
        # Walk the provider cascade EXCLUDING claude-code.
        from artemis.providers.errors import MissingApiKeyError, UnknownProviderError
        from artemis.providers.registry import get_adapter

        sandbox_adapter = None
        for candidate in ("anthropic", "openai", "openrouter", "gemini", "lm-studio", "codex"):
            try:
                sandbox_adapter = get_adapter(candidate)
                break
            except (MissingApiKeyError, UnknownProviderError):
                continue
            except Exception:
                continue

        if sandbox_adapter is None:
            return _json.dumps(
                {
                    "error": (
                        "test_run requires a tool-capable provider other than claude-code; "
                        "configure ANTHROPIC_API_KEY or another tool-capable provider "
                        "for sandbox execution."
                    )
                }
            )

        definition = _args.get("definition", {})
        if not isinstance(definition, dict):
            definition = {}
        result = await builder_engine.sandbox_run(
            definition,
            str(_args.get("prompt", "")),
            adapter=sandbox_adapter,
            allow_writes=False,
        )
        return _json.dumps(result, indent=2)

    # ── CC20 grounding tools ────────────────────────────────────────────────────

    if name == "builder_read_tool_signatures":
        from artemis.builder.grounding import extract_allowed_status_values
        from artemis.builders import repository as _builders_repo

        agent_id_arg = str(_args.get("agent_id", ""))
        if not agent_id_arg:
            return _json.dumps({"error": "agent_id is required"})

        try:
            agent = await _builders_repo.get_agent(session, agent_id_arg)
        except ValueError:
            return _json.dumps({"error": f"Agent not found: {agent_id_arg!r}"})

        from artemis.tools.registry import get_factory

        declared = agent.tools or []

        # Collect allowed status values once (shared across all tools for this agent).
        allowed_statuses = await extract_allowed_status_values(session)

        tool_entries: list[dict[str, _Any]] = []
        for entry in declared:
            t_name = entry if isinstance(entry, str) else str(entry.get("name", ""))
            tool_entry: dict[str, _Any] = {"name": t_name}

            factory = get_factory(t_name)
            if factory is not None:
                try:
                    from unittest.mock import MagicMock

                    stub = MagicMock()
                    stub.session = None
                    stub.agent_id = "__grounding_stub__"
                    stub.agent_db_id = 0
                    stub.agent_run_id = "__grounding_stub__"
                    stub.pipeline_run_id = None
                    tool_def, _impl = factory(stub)
                    tool_entry["description"] = tool_def.description
                    tool_entry["input_schema"] = tool_def.input_schema
                except Exception:
                    pass

            # Attach allowed status values for any status-related tool.
            if "status" in t_name.lower() or "queue" in t_name.lower():
                tool_entry["allowed_status_values"] = allowed_statuses

            tool_entries.append(tool_entry)

        return _json.dumps(
            {"agent_id": agent_id_arg, "tools": tool_entries},
            indent=2,
        )

    if name == "builder_read_db_schema":
        from artemis.builder.grounding import extract_db_constraints

        raw = _args.get("table_names", [])
        if not isinstance(raw, list):
            return _json.dumps({"error": "table_names must be an array of strings"})
        table_names = [str(t) for t in raw]
        if not table_names:
            return _json.dumps({"error": "table_names must be a non-empty array"})

        schema_result = await extract_db_constraints(session, table_names)
        return _json.dumps(schema_result, indent=2)

    if name == "builder_read_skill_catalog":
        from artemis.builder.grounding import extract_tool_registry

        kind_filter = str(_args.get("kind", "")) if _args.get("kind") else None

        catalog = await extract_tool_registry(session)

        if kind_filter == "tool":
            return _json.dumps(
                {"registered_tools": catalog["registered_tools"], "skills": []}, indent=2
            )
        if kind_filter == "skill":
            return _json.dumps({"registered_tools": [], "skills": catalog["skills"]}, indent=2)

        return _json.dumps(catalog, indent=2)

    # ── M2 memory retrieval tool ────────────────────────────────────────────────

    if name == "builder_search_memory":
        return await _dispatch_builder_search_memory(_args, session)

    return _json.dumps({"error": f"Unknown builder tool: {name}"})


async def _dispatch_builder_search_memory(
    args: dict[str, object],
    session: AsyncSession,
) -> str:
    """Retrieve curated memory observations for an agent (M2).

    Resolves scope agent:<agent_id>, calls search_observations, and returns
    each observation with its top-3 evidence links.  Empty scope returns [].
    Read-only — uses the passed-in session, no new session opened.
    """
    import json as _json
    from typing import Any as _Any

    from sqlalchemy import select as _sa_select

    from artemis.memory.models import MemoryScope
    from artemis.memory.retrieval import search_observations
    from artemis.memory.schemas import Scope
    from artemis.memory.store import list_evidence_for_observation

    agent_id_arg = str(args.get("agent_id", "")).strip()
    if not agent_id_arg:
        return _json.dumps({"error": "agent_id is required"})

    query_arg = str(args.get("query", "")).strip()
    limit_arg = min(int(args.get("limit", 10)), 50)  # type: ignore[call-overload]

    # Check whether the scope exists; if not, return [] (not error).
    scope_check = await session.execute(
        _sa_select(MemoryScope).where(
            MemoryScope.scope_kind == "agent",
            MemoryScope.scope_id == agent_id_arg,
        )
    )
    if scope_check.scalar_one_or_none() is None:
        return _json.dumps([])

    scope = Scope(scope_kind="agent", scope_id=agent_id_arg)
    # search_observations requires a non-empty query string for FTS/semantic;
    # fall back to a broad recency-only search when no query is provided.
    effective_query = query_arg or agent_id_arg
    observations = await search_observations(
        session=session,
        scope_set=[scope],
        query=effective_query,
        limit=limit_arg,
    )

    results: list[dict[str, _Any]] = []
    for obs in observations:
        evidence_links = await list_evidence_for_observation(session, obs.id)
        evidence_summary = [
            {
                "source_kind": ev.source_kind,
                "source_id": ev.source_id,
                "preview": ev.source_quote or "",
            }
            for ev in evidence_links[:3]
        ]
        results.append(
            {
                "id": obs.id,
                "content": obs.content,
                "created_at": obs.created_at.isoformat(),
                "confidence": obs.confidence,
                "superseded_by": obs.superseded_by,
                "evidence_summary": evidence_summary,
            }
        )

    return _json.dumps(results, indent=2)


# ── stdio entrypoint ────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m artemis.tools.mcp_server")
    # Agent-run scope (CC1/CC2) — mutually exclusive with builder scope.
    parser.add_argument("--agent-id", default=None, help="Dotted agent id to bind to.")
    parser.add_argument("--run-id", default=None, help="Agent run UUID (for provenance).")
    parser.add_argument(
        "--speaker-id",
        default=None,
        help="Slack user id of the person speaking, for identity-gated tools.",
    )
    parser.add_argument("--pipeline-run-id", default=None, help="Optional pipeline run id.")
    # Builder scope (CC19) — mutually exclusive with agent-run scope.
    parser.add_argument(
        "--builder-session-id",
        default=None,
        type=int,
        help="Builder session id (CC19 Builder MCP tools).",
    )
    # Floating Artemis scope — parent turn handler may pass repeated tool-name
    # allowlist entries so the subprocess mirrors the in-process registry.
    parser.add_argument(
        "--floating-session-id",
        default=None,
        help="Floating Artemis session id for auto-invoke tools.",
    )
    parser.add_argument(
        "--tool-name",
        action="append",
        default=None,
        help="Tool name to expose for Floating Artemis mode. Repeat per tool.",
    )
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


async def _serve_builder(builder_session_id: int) -> int:
    """Open a session and serve Builder-scoped MCP tools over stdio (CC19).

    Returns a process exit code (0 on clean shutdown).
    """
    async with SessionLocal() as session:
        logger.info(
            "artemis MCP server (builder) bound to builder_session_id=%s tools=%s",
            builder_session_id,
            BUILDER_MCP_TOOL_NAMES,
        )
        server = _build_builder_server(session, builder_session_id)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    return 0


async def _serve_floating_artemis(
    floating_session_id: str,
    tool_names: list[str] | None,
    trusted_agent_id: str | None = None,
    speaker_id: str | None = None,
) -> int:
    """Open a session and serve Floating Artemis auto-invoke tools over stdio.

    M3 (SECURITY): scope gating binds to ``trusted_agent_id`` when the parent
    forwards it — that value is derived from the LIVE caller's verified identity,
    so a non-owner running a turn (on their own or an owner's session) cannot read
    owner memory. Only when it is absent (tests / legacy callers) do we fall back
    to loading agent_id from persisted session metadata. If neither resolves,
    agent_id is None and every memory query returns empty (fail-closed).

    **Sets ``floating_session_id_var`` / ``floating_trusted_agent_id_var`` here,
    and that is load-bearing.** This function runs in a SUBPROCESS. The parent
    turn handler sets those contextvars in its own process, and contextvars do
    not cross a process boundary — so any tool that reads them was reading
    ``None`` no matter what the parent did.

    What that cost (2026-08-12): ``dispatch_research`` reads
    ``floating_session_id_var`` to resolve the Slack channel Argus should post
    findings to. It got None, resolution failed instantly, and the tool took an
    early-return path that skips both the DB row and the background task — while
    still returning ``{"status": "dispatched"}``. Callie relayed that in good
    faith to Jon and to Josh for five weeks. ``argus_research_requests`` was
    empty the entire time: Argus had never run once.
    """
    from artemis.floating_artemis.context import (
        floating_session_id_var,
        floating_speaker_id_var,
        floating_trusted_agent_id_var,
    )

    floating_session_id_var.set(floating_session_id)
    if trusted_agent_id:
        floating_trusted_agent_id_var.set(trusted_agent_id)
    if speaker_id:
        floating_speaker_id_var.set(speaker_id)

    async with SessionLocal() as session:
        # M3: prefer the trusted agent_id from the live caller; never trust
        # persisted metadata when a trusted value was forwarded.
        _fa_agent_id: str | None = (trusted_agent_id or "").strip().lower() or None
        if _fa_agent_id is None:
            try:
                from artemis.floating_artemis.chat import _load_session_context

                _fa_ctx = await _load_session_context(
                    session_id=floating_session_id,
                    db_session=session,
                    all_surfaces=set(),
                )
                _fa_agent_id = _fa_ctx.agent_id
            except Exception:
                logger.debug(
                    "floating MCP: could not resolve agent_id for session=%s — failing closed",
                    floating_session_id,
                )

        tool_set = await build_floating_artemis_tool_set(
            set(tool_names or []), agent_id=_fa_agent_id, speaker_id=speaker_id
        )
        logger.info(
            "artemis MCP server (floating_artemis) bound to session=%s agent_id=%s tools=%s",
            floating_session_id,
            _fa_agent_id,
            sorted(tool_set),
        )
        server = _build_floating_artemis_server(session, tool_set)
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

    # CC19: builder scope takes priority when --builder-session-id is given.
    if args.builder_session_id is not None:
        return anyio.run(_serve_builder, args.builder_session_id)

    if args.floating_session_id is not None:
        return anyio.run(
            _serve_floating_artemis,
            args.floating_session_id,
            args.tool_name,
            args.agent_id,
            args.speaker_id,
        )

    # Agent-run scope (CC1/CC2): both --agent-id and --run-id are required.
    if not args.agent_id or not args.run_id:
        print(
            "FATAL: must pass --builder-session-id (CC19), "
            "--floating-session-id (Floating Artemis), "
            "or both --agent-id and --run-id (CC1/CC2)",
            file=sys.stderr,
            flush=True,
        )
        return 2

    return anyio.run(
        _serve,
        args.agent_id,
        args.run_id,
        args.pipeline_run_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
