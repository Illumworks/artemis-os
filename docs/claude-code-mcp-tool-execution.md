# Claude-Code MCP Tool-Execution Architecture (Design)

**Status:** Draft for Jon's sign-off. Gates the build (CC1/CC2/CC3 streams below). This is the F4-equivalent design for the subscription era — the critical path to close Phase BH.
**Author:** Lead (Opus, 2026-05-27)
**Companion to:** `docs/tool-execution-architecture.md` (F4 — the in-process tool registry that P2/P3 shipped). This doc layers a claude-code-compatible execution path on top of that registry.

---

## Why this exists (the constraint)

Phase BH wired everything for scouts to emit signals — rich prompts (F2), tools (P2/P3), imperative invocation (F6). But the closing smoke produced zero signals: the `claude-code` provider adapter flattens to `claude --print` text and **cannot do tool-use**. Verified research (claude-code-guide, 2026-05-27):

- `claude -p` headless can use tools, but **only claude-code's built-in tools** — not our custom `signal_queue.write` etc.
- The **Claude Agent SDK** supports custom tools but requires `ANTHROPIC_API_KEY` — NOT subscription auth.
- **`claude -p --mcp-config <json>` is the ONLY subscription-compatible path to custom tools.** Claude connects to an MCP server, calls its tools autonomously, runs its own agent loop — on subscription auth, no API key.

Jon's constraint (2026-05-27): subscription-only (no API key, no per-token cost), with a path to add a key later. So this MCP path is mandatory, not optional.

`mcp>=1.7.0` is already a dependency. Binary discovery exists (`find_cli_binary("claude")`, `CLAUDE_BIN` override).

---

## The control model

This is a genuine shift from the in-process model, and it's inherent to the constraint:

- **In-process model (P2, used by AnthropicAdapter):** artemis's `run_turn` loop owns orchestration — calls the adapter, gets a `tool_use`, executes the tool via the registry, feeds the result back, loops.
- **MCP model (claude-code):** **claude-code is the agent runtime.** It runs its OWN loop — decides which tools to call, calls them via MCP, loops until done, returns a final result. Our MCP server is just the tool *provider*. Artemis's `run_turn` is **bypassed** for claude-code-provider agents.

Both models coexist, selected by `agent.provider` via the cascade:
- `provider=claude-code` → MCP path (subscription, this doc).
- `provider=anthropic` → in-process `run_turn` path (already built; the "add API key later" path — works the day a key exists, zero new code).

---

## Component 1 — The artemis tools MCP server

New entrypoint: `artemis/tools/mcp_server.py`, runnable as `python -m artemis.tools.mcp_server --agent-id <id> --run-id <uuid> [--pipeline-run-id <id>]`.

- Built with the Python MCP SDK (`mcp` dep). Serves over **stdio** (claude-code spawns it as a subprocess).
- On startup, reads its launch args → builds a `ToolContext` (agent_id, run_id, pipeline_run_id) + opens its **own** async DB session.
- **Scopes the tool surface to the agent:** loads the `Agent` row, reads `agent.tools`, and registers ONLY those tools from the shared `SCOUT/tool registry` (reusing the exact P2/P3 `ToolImpl`s via `get_factory(name)`). Tools the agent doesn't list are never exposed.
- Each MCP tool's handler calls the corresponding artemis `ToolImpl(arguments)` with the bound `ToolContext`, returns the string result.
- MCP tool names follow claude-code's convention: `mcp__artemis__<tool>` (e.g. `mcp__artemis__signal_queue_write`). The adapter maps `agent.tools` slugs → these MCP names for the allowlist.

**This is the centerpiece of "right tool for right agent":** the server is bound to exactly one agent and exposes exactly that agent's declared tools. The `agent.tools` column (F1/P1/F6 got it correct + spec-sourced) becomes the literal security allowlist.

## Component 2 — Claude-code adapter rework

`artemis/providers/claude_code/adapter.py` gains a tool-aware execution path. When a run has tools (`request.tools` non-empty / the agent declares tools):

1. Generate a per-run MCP config JSON pointing at `python -m artemis.tools.mcp_server` with this run's `--agent-id/--run-id/--pipeline-run-id` args.
2. Launch `claude -p --mcp-config <generated.json> --allowedTools mcp__artemis__<each of the agent's tools> --disallowedTools <built-ins>` (disable Bash/Read/Write/etc. so the agent has ONLY its MCP tools). Pipe the composed prompt (F2's rich system prompt + F6's imperative task) via stdin.
3. claude-code runs its autonomous loop: fetch → evaluate → call `signal_queue.write` (via MCP) → etc., until done.
4. Capture the final result text + usage. Signals were written to the DB by the MCP server process during the loop.

The existing text-only `complete()` path stays for non-tool runs (cascade fallback, simple completions).

**Bypassing run_turn:** `run_agent` (executor.py) must detect claude-code-provider + tool-using agents and route to this adapter path instead of the `run_turn` loop. The adapter returns the final result; the emitted signals are read from the DB (written by the MCP process). One clean branch in `run_agent`.

## Right-tool-for-right-agent — the four layers (Jon's question)

1. **Per-run scoping:** the MCP server exposes ONLY `agent.tools` for the bound agent. Other agents' tools aren't in its world.
2. **Built-ins disabled:** `--disallowedTools` (or allowlist to only `mcp__artemis__*`) so the agent can't reach Bash/files/web.
3. **Per-tool enforcement (defense-in-depth):** sensitive tools self-check the caller — `signal_queue.write` is scout-only + enforces the reason-code allowlist; `campaign_brief.write` is brief_assembler-only. (Already in P2/P3.)
4. **Identity isolation:** one MCP server process per run, bound to one agent_id/run_id. Concurrent runs = separate processes; no shared state, no cross-agent leakage.

## Per-run context handoff

The MCP server is a separate process, so context flows via **launch args** (agent_id, run_id, pipeline_run_id). The server opens its own DB session from those. This is the cleanest of the options (vs env-only or HTTP callback) because the server reuses artemis's code + models directly — it IS artemis, just a tool-serving entrypoint.

## Transaction boundary (the real subtlety)

Today, in-process tools use the caller's session; the pipeline executor owns the transaction (flush, then commit at the end). Under MCP, the server runs in a **separate process with its own session**, so:

- Signals written by `signal_queue.write` are committed by the **MCP server's** session, independent of the pipeline executor's transaction.
- This is actually fine — arguably better: signals persist as the scout emits them, regardless of whether downstream pipeline nodes succeed. But it's a real change from the "executor owns the txn" model.
- **Decision needed (see open questions):** does the MCP server commit per-tool-call, or at end of run? Lean: commit per successful write (signals are independent facts; a crash mid-run shouldn't lose already-emitted signals).

## Result flow

claude-code returns final assistant text (e.g. "Emitted 3 signals: ..."). The `agent_run` row records the text + usage. The actual signals are in `signal_queue` (written by the MCP process), linked by `provenance.agent_run_id`. The executor reads emitted-signal count from the DB for the node state / downstream gating.

## The "add API key later" path — already built

When/if Jon adds an `ANTHROPIC_API_KEY`: set those agents to `provider=anthropic`. The `AnthropicAdapter` + artemis `run_turn` loop already forward tools correctly (P2 proved this with the mock-LLM e2e). No new code. The two paths coexist; the cascade picks per agent. So this design does NOT close the door on the API path — it sits alongside it.

---

## Open questions for Jon's sign-off

1. **Transaction commit granularity** (above): MCP server commits per-write vs end-of-run. Lean: per successful write.
2. **Concurrency / process cost:** the marketing pipeline runs 9 scouts. Each tool-using scout spawns a `claude -p` + an MCP server subprocess. Sequential today (cheap). If we parallelize scouts later, that's 9× (claude-code + MCP) processes — acceptable but worth noting. Lean: keep scouts sequential for v1.
3. **claude-code loop limits:** claude-code has its own max-turn / cost behavior. Do we cap it (e.g. `--max-turns`)? Lean: set a sane `--max-turns` so a confused agent can't loop forever. (This is also where the cost cap re-enters — claude-code turns are subscription, but runaway loops waste wall-clock.)
4. **MCP server lifetime:** spawn per run (clean isolation, slight startup cost) vs a long-lived pool. Lean: per-run spawn for v1 (isolation > micro-optimization).
5. **Failure surfacing:** if claude-code errors or the MCP server crashes, how does the node fail cleanly? Lean: adapter catches, marks node failed with the error, pipeline continues (other scouts unaffected).

## Streams to build it

- **CC1 — MCP server** (`artemis/tools/mcp_server.py`): stdio MCP server, per-agent tool scoping, ToolContext from launch args, own session, reuses P2/P3 registry. Tests (scoping, a real tool call writes a signal). ~Claude Code Worker.
- **CC2 — Adapter rework + run_agent routing:** tool-aware claude-code launch (`--mcp-config`, `--allowedTools`, `--disallowedTools`, `--max-turns`), per-run config generation, bypass run_turn for claude-code tool runs, result capture. ~Claude Code Worker. Depends on CC1.
- **CC3 — End-to-end verification:** real pipeline run → real signals via subscription. Lead browser smoke. (Not a Worker stream — Lead-run after CC1+CC2 merge.)

CC1 → CC2 sequential (CC2 launches CC1's server). CC3 is the Lead close-out smoke.

---

## Why this is the right design (not just the only one)

- **Reuses everything we built.** The P2/P3 tool registry + ToolContext + the spec-sourced tool lists (F1/P1/F6) are reused verbatim — the MCP server is a thin serving layer over them. No throwaway.
- **The security boundary falls out of work already done.** `agent.tools` (already correct + spec-sourced) IS the per-run allowlist. We didn't build that for this — but it pays off here.
- **Doesn't foreclose the API path.** The day a key exists, `provider=anthropic` works through the existing in-process loop. Both coexist.
- **Honors the subscription constraint** — the entire point. Zero per-token API cost.

The cost is real provider engineering (CC1+CC2, ~1-2 weeks), and a control-model shift (claude-code owns the loop). But there is no subscription-compatible alternative, and this path is clean given the registry we already have.
