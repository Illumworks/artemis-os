# CC2 — Claude-Code Adapter Tool-Use + run_agent Routing (part 2 of 2)

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/cc2-adapter-tooluse`
**Browser smoke owner:** Lead (this session), post-merge — the real Phase-BH-closing pipeline smoke.
**Report back to me by:** Jon pastes terminal-Lead's relay into Lead chat
**LOC cap:** ~300 (enumerated; ship complete + report the diff rather than cutting integration or tests).
**Depends on:** CC1 (`worker/cc1-mcp-server`) MERGED. CC2 launches CC1's server — integrate against the REAL server, not a mock.
**Design reference:** `docs/claude-code-mcp-tool-execution.md`. Sign-offs locked: per-run spawn, `--max-turns` cap, catch-and-fail-the-node on error.

---

## Why this exists

CC1 built the MCP server that re-exposes our tools, scoped per agent. CC2 makes the claude-code provider actually USE it: for a tool-using agent, launch `claude -p --mcp-config <generated>` so claude-code autonomously calls our tools on subscription auth and runs its own loop. This is the step that makes scouts emit real signals.

---

## THE INTERFACE CONTRACT (from CC1 — consume exactly)

- MCP server launched as: `python -m artemis.tools.mcp_server --agent-id <id> --run-id <uuid> [--pipeline-run-id <id>]`, stdio, server name `artemis`.
- Tool naming: artemis `signal_queue.write` → MCP `signal_queue_write` → claude-code allowlist name `mcp__artemis__signal_queue_write`. CC1 exports `mcp_tool_name(artemis_name) -> str`; USE IT to build the allowlist (don't re-derive the transform).
- The server exposes ONLY the agent's tools. CC2's allowlist must list exactly those (`mcp__artemis__<each>`).

---

## Scope

### Part A — Tool-aware claude-code launch

In `artemis/providers/claude_code/adapter.py`, add a tool-capable execution path used when the run has tools. The existing text-only `complete()` stays for non-tool runs.

New method (or extended path), conceptually `run_with_tools(request, *, agent_id, run_id, pipeline_run_id, agent_tools: list[str]) -> CompletionResponse`:
1. Generate a per-run MCP config JSON (temp file) describing one stdio server named `artemis`: command `python` (use the same interpreter, `sys.executable`), args `["-m", "artemis.tools.mcp_server", "--agent-id", agent_id, "--run-id", run_id, ...]`. Match the MCP config schema claude-code expects (`mcpServers` map — verify the exact shape claude-code's `--mcp-config` wants).
2. Build the launch command:
   ```
   claude -p --output-format json --model <model>
     --mcp-config <generated.json>
     --allowedTools mcp__artemis__<t1> mcp__artemis__<t2> ...   (one per agent tool)
     --disallowedTools <claude-code built-ins: Bash, Read, Write, Edit, etc.>
     --max-turns <N>     (sane cap, e.g. 15 — sign-off Q3)
   ```
   (Verify the exact flag names/format against the installed claude CLI — `--allowedTools`/`--disallowedTools`/`--max-turns` spellings. Adapt to reality; report what the CLI actually accepts.)
3. Pipe the composed prompt (the F2 rich system prompt + F6 imperative task, already in `request.system` + `request.messages`) via stdin, same as today's `_flatten_to_prompt`.
4. Run the subprocess (reuse the timeout pattern). On success, parse the JSON result → final text + usage. Return a `CompletionResponse` with `stop_reason="end_turn"` (claude-code already ran its full loop; there are no further turns for artemis to orchestrate).
5. Clean up the temp MCP config file.
6. **Failure handling (sign-off Q5):** any error (non-zero exit, timeout, MCP launch failure) → raise a provider error that the caller surfaces as a failed node; the pipeline continues for other scouts.

### Part B — run_agent routing

In `artemis/builders/executor.py`, `run_agent` currently builds the tool registry and calls `run_turn`. Add a branch:

- If the resolved provider is **claude-code** AND the agent has tools → route to the adapter's `run_with_tools(...)` path (claude-code owns the loop). Do NOT run the in-process `run_turn` loop (it would double-orchestrate).
- Else (anthropic / other tool-capable provider, or no tools) → existing `run_turn` path unchanged (this is the "add API key later" path — leave it intact).

After a claude-code tool run, the emitted signals are already in the DB (written by the MCP server process). Record the agent_run with the returned text + usage. The node's emitted-signal count is read from the DB by the executor as it does today.

Keep this branch small and legible — one clear `if`. Don't refactor run_agent's structure.

### Part C — Tests

`artemis/providers/tests/test_claude_code_tooluse.py` + `artemis/builders/tests/test_run_agent_routing.py`:
1. **MCP config generation:** `run_with_tools` produces a config with the correct server command/args for the given agent_id/run_id, and an allowedTools list matching the agent's tools via `mcp_tool_name`. (Pure function — assert the generated config + command, don't launch claude.)
2. **Built-ins disallowed:** the launch command includes the built-in tools in `--disallowedTools`.
3. **max-turns present:** the command includes `--max-turns`.
4. **Routing:** `run_agent` with a claude-code provider + tool-using agent calls `run_with_tools` (mock it), NOT `run_turn`. With anthropic provider, calls `run_turn` (mock). With no tools, uses the text path.
5. **Failure surfacing:** a simulated subprocess failure → run_agent returns/raises such that the node is marked failed (assert the failure path).

Mock the subprocess (don't launch real claude in unit tests). The REAL launch is the Lead post-merge smoke.

---

## Files owned

- EDIT: `artemis/providers/claude_code/adapter.py` (add the tool path; keep `complete()` for text)
- EDIT: `artemis/builders/executor.py` (the routing branch in run_agent — small)
- NEW: `artemis/providers/tests/test_claude_code_tooluse.py`
- NEW or EXTEND: `artemis/builders/tests/test_run_agent_routing.py`

**Do not touch:** `artemis/tools/mcp_server.py` (CC1 — import its `mcp_tool_name` only), the registry/tools, run_turn internals, blueprints, the seed.

---

## Acceptance criteria (demonstrate each)

1. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/providers/tests/test_claude_code_tooluse.py artemis/builders/tests/test_run_agent_routing.py -v` — all pass. **Paste.**
2. Generated MCP config + launch command for regional_news. **Paste them** (Lead verifies allowedTools = regional_news's tools, built-ins disallowed, max-turns set).
3. The actual claude CLI flag names verified against the installed binary (`claude --help` excerpt for mcp-config/allowedTools/max-turns). **Paste the relevant lines.**
4. `./scripts/check.sh` passes modulo known-exempt j5b. **Paste summary.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cc2-adapter-tooluse`. **Paste.**

**Note:** CC2's unit tests prove the wiring. The end-to-end "real signals flow" proof is Lead's post-merge smoke (CC3) — a real pipeline run. Do NOT claim the loop closed; that's Lead's verification.

---

## Hard constraints

- Integrate against the REAL merged CC1 server (CC1 must be merged before this fires).
- Verify claude CLI flags against the installed binary; adapt to reality, report what you found.
- Keep `complete()` (text path) intact for non-tool runs + cascade fallback.
- Keep the anthropic/`run_turn` path intact (the add-key-later path).
- Local-only git. Worker commits on `worker/cc2-adapter-tooluse`; terminal-Lead merges after Lead approves.

---

## Report-back format

```
CC2 — Claude-Code Adapter Tool-Use report
1. Commit / branch / worktree
2. LOC diff stats
3. Test pass summary (acceptance #1)
4. Generated MCP config + launch command for regional_news (acceptance #2)
5. Verified claude CLI flags (acceptance #3) — actual flag names/spellings
6. check.sh summary
7. Anything surprising — especially claude CLI flag differences vs this brief's assumptions
```

---

**Claude Code Worker: the claude CLI's real flag names are the biggest unknown — verify `--mcp-config`, `--allowedTools`, `--disallowedTools`, `--max-turns` against the installed binary BEFORE coding the launch, and report the truth. Operating principle: never assume a flag exists; check `claude --help`.**
