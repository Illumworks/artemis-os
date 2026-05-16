"""Artemis Memory — Read-only MCP server (B4).

Exposes six read-only tools over stdio transport so an external Claude Code
instance can search and inspect Artemis memory without touching the HTTP server.

Tools:
  memory_search                — fusion search over observations
  memory_get_observation       — fetch one observation + evidence chain
  memory_get_drawer            — fetch one drawer
  memory_list_scopes           — list all memory scopes
  memory_list_entities         — list entities in a scope
  memory_get_entity_neighborhood — entity + N-hop relations

All tools are read-only. No write tools in V1 (§1.5 of the keystone plan).

Usage (register in Claude Code's MCP config):
  {
    "artemis-memory": {
      "command": "uv",
      "args": ["run", "python", "-m", "artemis.mcp.memory_server"],
      "env": { "ARTEMIS_DB_URL": "postgresql+asyncpg://..." }
    }
  }
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

_logger = logging.getLogger(__name__)

# ── Tool handler implementations (exported for unit testing) ──────────────────


async def handle_memory_search(
    scope_set: list[dict[str, str]] | None,
    query: str,
    limit: int,
    as_of_ts: int | None,
    *,
    session_factory: Any = None,
) -> list[dict[str, Any]]:
    """Fusion search over memory observations."""
    from artemis.memory.retrieval import search_observations
    from artemis.memory.schemas import Scope

    scopes = (
        [Scope(scope_kind=s["scopeKind"], scope_id=s["scopeId"]) for s in scope_set]
        if scope_set
        else [Scope(scope_kind="workspace", scope_id="default")]
    )
    as_of = datetime.fromtimestamp(as_of_ts, tz=UTC) if as_of_ts is not None else None

    _get_session = session_factory or _default_session_factory()
    async with _get_session() as session:
        results = await search_observations(session, scopes, query, limit=limit, as_of=as_of)
    return [r.model_dump(mode="json") for r in results]


async def handle_memory_get_observation(
    obs_id: int,
    *,
    session_factory: Any = None,
) -> dict[str, Any] | None:
    """Fetch one observation plus its evidence chain."""
    from artemis.memory.store import get_observation, list_evidence_for_observation

    _get_session = session_factory or _default_session_factory()
    async with _get_session() as session:
        obs = await get_observation(session, obs_id)
        if obs is None:
            return None
        evidence = await list_evidence_for_observation(session, obs_id)
    return {
        **obs.model_dump(mode="json"),
        "evidence": [e.model_dump(mode="json") for e in evidence],
    }


async def handle_memory_get_drawer(
    drawer_id: int,
    *,
    session_factory: Any = None,
) -> dict[str, Any] | None:
    """Fetch one drawer by ID."""
    from artemis.memory.store import get_drawer

    _get_session = session_factory or _default_session_factory()
    async with _get_session() as session:
        drawer = await get_drawer(session, drawer_id)
    return drawer.model_dump(mode="json") if drawer is not None else None


async def handle_memory_list_scopes(
    filter_str: str | None,
    *,
    session_factory: Any = None,
) -> list[dict[str, Any]]:
    """List all memory scopes, optionally filtered by scope_kind or scope_id."""
    from sqlalchemy import select

    from artemis.memory.models import MemoryScope
    from artemis.memory.schemas import ScopeRead

    _get_session = session_factory or _default_session_factory()
    async with _get_session() as session:
        result = await session.execute(select(MemoryScope).order_by(MemoryScope.created_at))
        rows = [ScopeRead.model_validate(row) for row in result.scalars()]
    if filter_str:
        rows = [r for r in rows if r.scope_kind == filter_str or r.scope_id == filter_str]
    return [r.model_dump(mode="json") for r in rows]


async def handle_memory_list_entities(
    scope_set: list[dict[str, str]] | None,
    kind: str | None,
    limit: int,
    *,
    session_factory: Any = None,
) -> list[dict[str, Any]]:
    """List entities in a scope, optionally filtered by kind."""
    from artemis.memory.graph import list_entities_for_scope

    scopes = scope_set or [{"scopeKind": "workspace", "scopeId": "default"}]
    _get_session = session_factory or _default_session_factory()
    all_entities: list[dict[str, Any]] = []
    async with _get_session() as session:
        for s in scopes:
            entities = await list_entities_for_scope(
                session,
                s["scopeKind"],
                s["scopeId"],
                kind=kind,
                limit=limit,
            )
            all_entities.extend(e.model_dump(mode="json") for e in entities)
    return all_entities


async def handle_memory_get_entity_neighborhood(
    entity_id: int,
    hops: int,
    *,
    session_factory: Any = None,
) -> dict[str, Any] | None:
    """Return an entity + its N-hop relation neighborhood."""
    from artemis.memory.graph import get_entity_neighborhood

    _get_session = session_factory or _default_session_factory()
    async with _get_session() as session:
        result = await get_entity_neighborhood(session, entity_id, hops=hops)
    return result.model_dump(mode="json") if result is not None else None


# ── Session factory ───────────────────────────────────────────────────────────


def _default_session_factory() -> Any:
    from artemis.db import SessionLocal

    return SessionLocal


# ── MCP server factory ────────────────────────────────────────────────────────


def create_mcp_memory_server(session_factory: Any = None) -> Server:
    """Build and return the MCP Server instance.

    session_factory: optional callable that returns an async context manager
    yielding an AsyncSession. Defaults to the production SessionLocal.
    """
    server: Server = Server("artemis-memory")
    sf = session_factory  # captured in closures below

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="memory_search",
                description="Search Artemis memory observations using full-text, semantic, graph, recency, or hybrid fusion reranking.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope_set": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "scopeKind": {"type": "string"},
                                    "scopeId": {"type": "string"},
                                },
                                "required": ["scopeKind", "scopeId"],
                            },
                            "description": "Scope pairs to search. Defaults to workspace:default when omitted.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Search query (empty string = recency/score modes only).",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 50,
                            "description": "Max observations to return (1–50, default 10).",
                        },
                        "as_of": {
                            "type": "integer",
                            "description": "Unix-second timestamp for point-in-time validity filter.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_get_observation",
                description="Retrieve a single memory observation by ID, including its evidence chain.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Observation row ID."},
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="memory_get_drawer",
                description="Retrieve a single memory drawer by ID.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Drawer row ID."},
                    },
                    "required": ["id"],
                },
            ),
            Tool(
                name="memory_list_scopes",
                description="List all memory scopes. Optionally filter by scope_kind or exact scope_id.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "filter": {
                            "type": "string",
                            "description": "Filter by scope_kind (e.g. 'project') or exact scope_id.",
                        },
                    },
                },
            ),
            Tool(
                name="memory_list_entities",
                description="List named entities extracted from memory observations for a scope.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope_set": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "scopeKind": {"type": "string"},
                                    "scopeId": {"type": "string"},
                                },
                                "required": ["scopeKind", "scopeId"],
                            },
                            "description": "Scope pairs. Defaults to workspace:default.",
                        },
                        "kind": {
                            "type": "string",
                            "enum": [
                                "person",
                                "project",
                                "brand",
                                "campaign",
                                "post",
                                "channel",
                                "other",
                            ],
                            "description": "Filter to a specific entity kind.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 200,
                            "description": "Max entities per scope (default 50).",
                        },
                    },
                },
            ),
            Tool(
                name="memory_get_entity_neighborhood",
                description="Retrieve an entity and its relationship neighborhood (1–2 hops).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "Entity row ID."},
                        "hops": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 2,
                            "description": "Hop depth (1 or 2, default 1).",
                        },
                    },
                    "required": ["id"],
                },
            ),
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            match name:
                case "memory_search":
                    result = await handle_memory_search(
                        scope_set=arguments.get("scope_set"),
                        query=arguments.get("query", ""),
                        limit=int(arguments.get("limit", 10)),
                        as_of_ts=arguments.get("as_of"),
                        session_factory=sf,
                    )
                    return [TextContent(type="text", text=json.dumps(result))]

                case "memory_get_observation":
                    obs = await handle_memory_get_observation(
                        int(arguments["id"]), session_factory=sf
                    )
                    if obs is None:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps(
                                    {"error": f"Observation {arguments['id']} not found"}
                                ),
                            )
                        ]
                    return [TextContent(type="text", text=json.dumps(obs))]

                case "memory_get_drawer":
                    drawer = await handle_memory_get_drawer(
                        int(arguments["id"]), session_factory=sf
                    )
                    if drawer is None:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps({"error": f"Drawer {arguments['id']} not found"}),
                            )
                        ]
                    return [TextContent(type="text", text=json.dumps(drawer))]

                case "memory_list_scopes":
                    scopes = await handle_memory_list_scopes(
                        filter_str=arguments.get("filter"), session_factory=sf
                    )
                    return [TextContent(type="text", text=json.dumps(scopes))]

                case "memory_list_entities":
                    entities = await handle_memory_list_entities(
                        scope_set=arguments.get("scope_set"),
                        kind=arguments.get("kind"),
                        limit=int(arguments.get("limit", 50)),
                        session_factory=sf,
                    )
                    return [TextContent(type="text", text=json.dumps(entities))]

                case "memory_get_entity_neighborhood":
                    hood = await handle_memory_get_entity_neighborhood(
                        entity_id=int(arguments["id"]),
                        hops=int(arguments.get("hops", 1)),
                        session_factory=sf,
                    )
                    if hood is None:
                        return [
                            TextContent(
                                type="text",
                                text=json.dumps({"error": f"Entity {arguments['id']} not found"}),
                            )
                        ]
                    return [TextContent(type="text", text=json.dumps(hood))]

                case _:
                    return [
                        TextContent(
                            type="text", text=json.dumps({"error": f"Unknown tool: {name}"})
                        )
                    ]

        except Exception as exc:
            _logger.exception("MCP tool %r failed", name)
            return [TextContent(type="text", text=json.dumps({"error": str(exc)}))]

    return server


# ── Entry point (stdio) ───────────────────────────────────────────────────────


async def _run_stdio() -> None:
    server = create_mcp_memory_server()
    async with stdio_server() as (read_stream, write_stream):
        init_options = InitializationOptions(
            server_name="artemis-memory",
            server_version="1.0.0",
            capabilities=server.get_capabilities(
                notification_options=None,  # type: ignore[arg-type]
                experimental_capabilities={},
            ),
        )
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    import asyncio

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_run_stdio())
