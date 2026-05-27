# CC1 — Artemis Tools MCP Server (subscription tool-use, part 1 of 2)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/cc1-mcp-server`
**Browser smoke owner:** n/a (backend; Lead verifies standalone + via CC2's e2e later)
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** ~300 (full-diff insertions incl. tests). This is enumerated work — if the server + per-agent scoping + tests genuinely need more, ship it complete and report the diff; do NOT cut the scoping or tests to hit a number (calibration lesson from P3).
**Design reference:** `docs/claude-code-mcp-tool-execution.md` (read fully) + `docs/tool-execution-architecture.md` (the registry you're re-exposing). Sign-offs locked: per-write commit, per-run spawn.

---

## Why this exists

Scouts can't emit signals on the Claude Code subscription because the claude-code provider can't do custom tool-use. The only subscription-compatible path is `claude -p --mcp-config` → an MCP server that exposes our tools. This brief builds that server. CC2 (next) reworks the adapter to launch it. Together they close the Phase BH loop.

This server **reuses the existing P2/P3 tool registry verbatim** — it's a thin stdio serving layer, not a reimplementation.

---

## THE INTERFACE CONTRACT (CC2 depends on this — do not deviate without pinging Lead)

- **Invocation:** `python -m artemis.tools.mcp_server --agent-id <dotted_id> --run-id <uuid> [--pipeline-run-id <id>]`
- **Transport:** stdio (claude-code spawns it as a subprocess).
- **MCP server name:** `artemis`
- **Tool naming:** each artemis tool name has its dots replaced by underscores for the MCP tool name. e.g. artemis `signal_queue.write` → MCP tool `signal_queue_write`. Claude-code will see it as `mcp__artemis__signal_queue_write`. Provide a helper `mcp_tool_name(artemis_name: str) -> str` and its inverse, so CC2 can build the `--allowedTools` list deterministically.
- **Scoping:** the server exposes ONLY the tools in the bound agent's `agent.tools` list (intersected with the known registry). Nothing else.
- **Exit:** the server runs until stdin closes (claude-code manages lifetime). Clean shutdown closes the DB session.

---

## Scope

### Part A — The server entrypoint

`artemis/tools/mcp_server.py`, runnable as `python -m artemis.tools.mcp_server ...`:

1. Parse args: `--agent-id` (required), `--run-id` (required), `--pipeline-run-id` (optional).
2. Open an async DB session (own engine/session — this is a separate process; reuse `artemis.db` session factory).
3. Load the `Agent` row by agent_id. Fail clearly (stderr + non-zero exit) if not found.
4. Build a `ToolContext(session, agent_id, agent_db_id=agent.id, agent_run_id=run_id, pipeline_run_id=...)`.
5. Read `agent.tools` (list of dotted tool names). For each that exists in the P2/P3 registry (`artemis.tools.registry.get_factory`), instantiate `(tool_def, impl)` with the ToolContext and register it as an MCP tool named `mcp_tool_name(name)`. Skip unknown names (log to stderr). **Expose ONLY these.**
6. Serve over stdio using the `mcp` SDK (the dependency is `mcp>=1.7.0`). Each MCP tool handler: receive arguments dict → call the artemis `impl(arguments)` (async) → return the string result as MCP tool output.
7. **Per-write commit (signed-off Q1):** after any tool whose impl performed a DB write succeeds, `await session.commit()`. Simplest correct approach: commit after every successful tool call (reads are no-ops to commit). A tool returning a `VALIDATION_ERROR`/`PERMISSION_DENIED` string did not write — still safe to commit (no-op). On exception, rollback + return an error string (never crash the server mid-session).

### Part B — MCP SDK wiring

Use the Python MCP SDK's stdio server. Inspect the installed `mcp` package API (the Worker should check the actual installed version's server API — likely `mcp.server.Server` + `stdio_server`, or `FastMCP`). Pin tool input schemas from each artemis `Tool.input_schema` (they're already JSON-schema dicts). Pick whichever MCP SDK server API the installed version supports cleanly; document the choice.

### Part C — Tests

`artemis/tools/tests/test_mcp_server.py` (use `ARTEMIS_TEST_DB_URL`):
1. **Per-agent scoping:** build the server's tool set for `marketing.scout.regional_news` → assert it exposes exactly the MCP-named versions of regional_news's `agent.tools` (after F6, that includes `signal_queue_write`, `news_api_search`, etc.) and NOTHING else (e.g. no `legiscan_search`).
2. **Different agent, different tools:** `marketing.scout.legislative` exposes `legiscan_*` but NOT `news_api_search` unless it lists it.
3. **A tool call writes a signal:** invoke the registered `signal_queue_write` handler with a valid regional_news signal payload → assert a `signal_queue` row lands with the right `provenance.agent_run_id` + the session committed (query in a fresh session to prove the commit).
4. **Permission still enforced:** `signal_queue_write` for a non-scout agent context → `PERMISSION_DENIED`, no row.
5. **`mcp_tool_name` round-trips:** `signal_queue.write` ↔ `signal_queue_write`.
6. **Unknown tool in agent.tools:** skipped, server still starts, logs to stderr.

Factor the server's tool-set construction into a testable function (e.g. `build_tool_set(session, agent_id, run_id, pipeline_run_id) -> dict[str, (Tool, ToolImpl)]`) so tests don't need a live stdio transport. Test the stdio serving separately/minimally if practical.

### Part D — Standalone smoke (Worker runs it, pastes output)

Launch the server against the dev DB and confirm it lists the right tools. Since stdio needs an MCP client, a simple approach: a tiny test-harness script OR use the MCP SDK's client to list tools. At minimum, prove `build_tool_set(...)` for regional_news returns the expected scoped set, and that calling the signal_queue_write impl through it writes a row (Part C #3 covers this).

---

## Files owned

- NEW: `artemis/tools/mcp_server.py`
- NEW: `artemis/tools/tests/test_mcp_server.py`
- (If a tiny shared helper is needed, e.g. `mcp_tool_name`, put it in `mcp_server.py` and export it.)

**Do not touch:** the registry/context/tool files (P2/P3 — import + reuse only), the adapter (CC2 owns it), executor.py, run_turn, blueprints, the seed.

---

## Acceptance criteria (demonstrate each)

1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/tools/tests/test_mcp_server.py -v` — all pass. **Paste.**
2. Scoping proof: `build_tool_set` for regional_news lists exactly its tools (MCP-named); legislative lists its own. **Paste both tool-name lists.**
3. Write proof: the signal_queue_write path writes a committed `signal_queue` row (verified in a fresh session). **Paste the row.**
4. The installed `mcp` SDK server API used (which class/function). **Paste a one-line note.**
5. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste summary.**
6. `git diff --stat` + `git log --oneline -1` on `worker/cc1-mcp-server`. **Paste.**

---

## Hard constraints

- Reuse the P2/P3 registry + ToolImpls verbatim. Do not reimplement tools.
- The server is a SEPARATE process with its OWN session — that's intended (per-write commit).
- Honor the INTERFACE CONTRACT exactly (CC2 is being written against it in parallel).
- Local-only git. Worker commits on `worker/cc1-mcp-server`; terminal-Lead merges after Lead approves.

---

## Report-back format

```
CC1 — Artemis MCP Server report
1. Commit / branch / worktree
2. LOC diff stats
3. Test pass summary (acceptance #1)
4. Scoping proof — regional_news vs legislative tool lists (acceptance #2)
5. Write proof — committed signal row (acceptance #3)
6. MCP SDK API used (acceptance #4)
7. check.sh summary
8. Anything surprising — especially MCP SDK API mismatches vs the design's assumptions
```

---

**Claude Code Worker: read both design docs first. Operating principle — if the installed `mcp` SDK's server API differs from what this brief assumes, adapt to the REAL API and report it; don't force a shape that doesn't exist. The interface contract (invocation args, server name, tool naming) is the one thing that must not drift — CC2 depends on it.**
