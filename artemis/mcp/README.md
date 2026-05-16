# Artemis Memory MCP Server

Read-only MCP server that exposes Artemis memory to external Claude Code instances via stdio transport.

## Tools

| Tool | Description |
|------|-------------|
| `memory_search` | Fusion search (FTS + semantic + graph + recency + score) |
| `memory_get_observation` | Fetch one observation + evidence chain |
| `memory_get_drawer` | Fetch one drawer |
| `memory_list_scopes` | List all memory scopes |
| `memory_list_entities` | List entities in a scope, filter by kind |
| `memory_get_entity_neighborhood` | Entity + N-hop relation graph |

All tools are read-only. No write tools in V1.

## Registration

Add to Claude Code's MCP server config (`.claude/mcp_servers.json` or equivalent):

```json
{
  "artemis-memory": {
    "command": "uv",
    "args": ["run", "python", "-m", "artemis.mcp.memory_server"],
    "cwd": "/Users/artemis/Desktop/Artemis/artemis-os",
    "env": {
      "ARTEMIS_DB_URL": "postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_os"
    }
  }
}
```

## scope_set format

All tools that accept `scope_set` use this shape:
```json
[{"scopeKind": "workspace", "scopeId": "default"}]
```

When omitted, `scope_set` defaults to `workspace:default`.

## Validity windows

`memory_search` accepts `as_of` as a Unix-second integer for point-in-time queries.
Observations with `valid_until < as_of` or `valid_from > as_of` are excluded.

## Switching from stub to real

No stub here — the MCP server always talks to the real Postgres. Ensure `ARTEMIS_DB_URL`
is set before starting the server.
