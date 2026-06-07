# CC19 — Builder Tool Execution via MCP (Close Layer 4 of Self-Improvement Hollowness)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cc19-builder-mcp-tools`
**Browser smoke owner:** Lead, post-merge — open Builder for an agent with trajectory summaries, send a message asking for review + proposals, verify `definition_proposals` gets a row.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~600 (MCP server extension + adapter routing + Builder integration + tests).
**Priority:** CRITICAL — this is the structural blocker for the self-improvement consumer side. Without it, the Builder LLM can analyze runs but cannot land proposals. `definition_proposals` has been stuck at 0 rows since the platform started despite CC10-CC18 producing 35 trajectory summaries.

---

## Why this exists — the Layer 4 diagnosis

Lead ran a manual smoke (Builder session 11, target=`marketing.qualifier.brief_composer`, 4 trajectory summaries available). The Builder LLM did substantive diagnostic work:

> "Three clear patterns across the runs. [Pattern 1] Hallucinated lifecycle state — Run #329 called `signal_queue.update_status` with `'pending_human_review'`, a state that doesn't exist. [Pattern 2] N+1 probe loop with no dedup — 80+ sequential `signal_queue.get` calls. [Pattern 3] Tool skipping / no durable write — in 2 of 3 runs, brief was composed but never written to `signal_queue`."

…and drafted a complete revised definition with system prompt, tool delta, and rationale. **Then it could not call `propose()`.** Its own response in turn 2:

> "The `propose` tool isn't wired into this session's tool catalog — I can see it's referenced in the system prompt but isn't [a callable tool here]. I'll surface both proposals in full so you have everything you need to review and action them."

### Root cause (precise)

`artemis/providers/claude_code/adapter.py:100-162` — `ClaudeCodeAdapter.complete()`:

1. Receives `request.tools` from caller
2. **Ignores it entirely.** Flattens the conversation to a single prompt via `_flatten_to_prompt`
3. Runs `claude --print --output-format json`
4. Returns text response with `stop_reason="end_turn"` — never `"tool_use"`

The Builder loop at `agent_builder.py:464` calls `adapter.complete(request)` with `tools=tool_specs` populated. The adapter drops the tools. The Builder LLM has no `propose` to call.

Meanwhile, the SAME adapter has `run_with_tools()` at line 164 — used by marketing pipeline agents — which properly supports tools via the Artemis MCP server (CC1/CC2). Two adapter methods, only one tool-capable.

### The other-providers picture

All non-claude-code adapters handle tools correctly in `.complete()`:

| Adapter | Tools in `.complete()` |
|---|---|
| `anthropic` | ✅ Native tool_use loop |
| `gemini` | ✅ Function-calling translated |
| `openai` | ✅ `tool_calls` ↔ `tool_use` translation |
| `openrouter` | ✅ Same as openai |
| `claude-code` | ❌ in `.complete()`; ✅ in `.run_with_tools()` via MCP |
| `codex` | ❌ Text-only (172 LOC) |
| `lm-studio` | ❌ Text-only (47 LOC) |

The asymmetry only exists in claude-code — and only because the subscription CLI doesn't expose a native tool_use turn-by-turn API; it runs its own internal agent loop with MCP. The fix preserves subscription-only by routing claude-code's `.complete()` through MCP when tools are present.

---

## Scope

### Part A — Add Builder tools to the Artemis MCP server

In `artemis/tools/mcp_server.py` (the CC1 MCP server), add a NEW scope dimension for Builder-session-scoped tools. The existing scope dimensions are `agent_id` / `run_id` / `pipeline_run_id` (for pipeline agents). Add `builder_session_id` as the fourth.

Register five new MCP tools, scoped by `builder_session_id`:

1. **`builder_read_existing`** — input: `kind` ('agent'|'skill'|'workflow'), `limit` (default 50). Calls `engine.read_existing(kind, db_session=..., limit=...)`. Returns JSON.
2. **`builder_read_capabilities`** — no input. Calls `engine.read_capabilities(db_session=...)`. Returns JSON.
3. **`builder_read_recent_runs`** — input: `agent_id` (string), `limit` (default 10). Calls `engine.read_recent_runs(agent_id, db_session=..., limit=...)`. Records returned PKs in a per-session `_seen_run_ids` set (same validation pattern as current `_propose`).
4. **`builder_propose`** — input: `kind` ('agent'|'skill'), `definition` (dict), `target_id` (optional int), `citations` (optional dict with `run_ids` list). Validates citations against `_seen_run_ids` (rejects fabricated IDs). Calls `engine.propose(...)` with `proposed_by="builder"` and `builder_session_id=...`. Returns `{"proposal_id": ..., "status": "pending"}`.
5. **`builder_test_run`** — input: `definition` (dict), `prompt` (string). Calls `engine.sandbox_run(definition, prompt, adapter=<some_adapter>, allow_writes=False)`. **Recursion concern:** when claude-code is invoking this tool, the test_run inside MCP needs a DIFFERENT adapter to actually run the sandbox. Walk the provider cascade EXCLUDING the currently-active claude-code subprocess. If only claude-code is available, return `{"error": "test_run requires a tool-capable provider other than claude-code; configure ANTHROPIC_API_KEY or another tool-capable provider for sandbox execution."}`. Document this limitation.

**Scoping integration**: the MCP server needs to look up `builder_session_id` at tool call time to pass to `engine.propose` and to bind the `db_session`. Same shape as existing per-run scoping but the context-var key is `builder_session_id` instead of `run_id`/`pipeline_run_id`. Establish the context-var before launching the subprocess (see Part B).

### Part B — Route `ClaudeCodeAdapter.complete()` through MCP when tools are present

In `artemis/providers/claude_code/adapter.py`:

```python
async def complete(self, request: CompletionRequest) -> CompletionResponse:
    if request.tools:
        # Route through MCP for tool support (similar to run_with_tools
        # but scoped for arbitrary surface, not just pipeline agents).
        return await self._complete_with_tools(request)
    # ... existing text-only path
```

Add `_complete_with_tools(self, request)` method:

1. Determine scoping context. For Builder, the caller will pass scoping via a CONTEXT-VAR (`builder_session_id`) that the MCP server reads. For non-Builder tool-using surfaces that route through `.complete()` (Floating Artemis, Pipeline AI Panel), they need to declare their own scope context. **For CC19's scope, we wire Builder only.** Floating Artemis + Pipeline AI Panel route through MCP later via separate briefs.
2. Build MCP config with `builder_session_id` set (analogous to `_build_mcp_config` for runs).
3. Launch `claude -p --mcp-config <tmp> --strict-mcp-config` with the appropriate `--allowedTools` filter listing only the builder_* tools.
4. Translate the request's tool specs into the MCP allowed-tools list (the tool NAMES become claude-code's allowed list; the SPEC details live in the MCP server registration).
5. Parse the result. Because claude-code's internal agent loop handles the tool-use turns, the final result returned to the caller is the final text answer. The tool calls happen inside the subprocess and the proposal row lands in the DB via the MCP server's `_propose` implementation.

**Critical design decision:** unlike standard tool_use where the caller gets back tool_use blocks and re-issues with tool_result, claude-code's internal loop COMPLETES the tool-use cycle inside the subprocess. The Builder's existing `handle_turn_stream` loop iterates up to 5 times expecting `stop_reason == "tool_use"` and re-issuing — that iteration becomes a NO-OP for claude-code's tool path because claude-code returns the final text in one shot. **The Builder loop should detect this case and not iterate.** Implementation: if `response.stop_reason == "end_turn"` AND the response contains no `tool_use` blocks AND the conversation has new proposal rows (queryable via `db_session`), the turn is complete.

Even simpler approach: have `_complete_with_tools` return a `CompletionResponse` with the final assistant text and synthesize the tool_use blocks IF the subprocess emitted them in its trace (claude-code can be asked to surface this via `--output-format stream-json` but for CC19's scope, the simpler "final text + DB side-effect" model is acceptable).

### Part C — Builder integration (minimal — most work is in adapter + MCP)

In `artemis/builder/agent_builder.py`:

- The `handle_turn_stream` loop at line 455 iterates 5 times to handle tool_use rounds. When the active adapter is `ClaudeCodeAdapter` and tools are present, this iteration is unnecessary (claude-code's internal loop already ran). Add a check at the start of the loop: if `isinstance(adapter, ClaudeCodeAdapter)` AND tools present, run one iteration and exit early when `stop_reason == "end_turn"`.
- The current in-process tool implementations (`_read_existing`, `_propose`, etc. in `build_tool_registry`) become DEAD CODE for the claude-code path because claude-code's MCP path executes the tools directly. **Leave them in place for now** — they may be needed when other adapters (anthropic, gemini) drive the Builder. But add a comment explaining the duality.
- Pass `builder_session_id` to the adapter via a contextvar set BEFORE the adapter call. Pattern:

```python
from artemis.builder.context import builder_session_id_var

token = builder_session_id_var.set(builder_session_id)
try:
    response = await adapter.complete(request)
finally:
    builder_session_id_var.reset(token)
```

Create `artemis/builder/context.py` with the contextvar definition. The MCP server reads this contextvar when handling tool calls.

### Part D — Tests

`artemis/builder/tests/test_cc19_mcp_tool_execution.py`:

1. **Integration test (highest priority):** create a Builder session with `target_id` pointing to an agent that has trajectory summaries (set up via fixtures). Send a message asking for review + proposals. Verify `definition_proposals` table gets a new row with `proposed_by="builder"`, `target_id` set, `kind="agent"`, valid `proposed_definition` JSONB. This is the END-TO-END test that proves the loop closes.
2. **MCP tool unit tests:** each of the 5 new MCP tools called directly with a fixture-built scope context. Verify return shapes and side-effects.
3. **Adapter routing test:** call `ClaudeCodeAdapter.complete()` with `tools=[]` (text-only path) and `tools=[propose_spec]` (MCP path). Verify routing.
4. **citations validation:** verify `builder_propose` rejects citations referencing run_ids not previously returned by `builder_read_recent_runs` in the same session.
5. **Test_run recursion:** verify `builder_test_run` correctly walks the provider cascade EXCLUDING claude-code when claude-code is the active adapter.

### Part E — Provider cascade clarity (documentation, no code)

Update `artemis/providers/registry.py` docstrings to explicitly note:

- Tool-capable adapters: anthropic, gemini, openai, openrouter (via standard `.complete()`)
- claude-code: tool-capable ONLY via MCP path (`.complete()` with tools, OR `.run_with_tools()`)
- codex, lm-studio: text-only fallbacks. Calling them with `request.tools` populated will silently lose tools — emit a `logger.warning` when this happens so future hollowness gets caught immediately.

Add to `artemis/providers/codex/adapter.py` and `artemis/providers/lm_studio/adapter.py` `.complete()` methods:

```python
if request.tools:
    logger.warning(
        "%s adapter received request.tools but does not support tool execution. "
        "Tools will be ignored. Consider routing tool-using surfaces to a tool-capable provider.",
        type(self).__name__,
    )
```

---

## Files owned

- EDIT: `artemis/tools/mcp_server.py` — add 5 Builder MCP tools + builder_session_id scoping
- EDIT: `artemis/providers/claude_code/adapter.py` — add `_complete_with_tools` method, route `.complete()` when tools present
- EDIT: `artemis/builder/agent_builder.py` — minimal: contextvar set/reset, short-circuit the 5-iteration loop for claude-code-with-tools path
- NEW: `artemis/builder/context.py` — `builder_session_id_var` contextvar
- EDIT: `artemis/providers/codex/adapter.py`, `artemis/providers/lm_studio/adapter.py` — warning log when tools present
- EDIT: `artemis/providers/registry.py` — docstring update
- NEW: `artemis/builder/tests/test_cc19_mcp_tool_execution.py` — integration + unit tests
- (Likely): `artemis/builder/engine.py` — verify `engine.propose` accepts `builder_session_id` kwarg cleanly; no schema change expected

---

## Acceptance criteria

1. **Migration check** — no schema changes required for CC19. Confirm none added.
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/builder/tests/test_cc19_mcp_tool_execution.py -v` — all pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. **End-to-end smoke (Lead does this post-merge):**
   - Create Builder session for `marketing.qualifier.brief_composer` via API: `POST /api/builder/sessions` with `target_id=17`
   - Send message: "Please review this agents recent runs and propose specific improvements based on what you see in the trajectory summaries."
   - Wait for response (up to 5 minutes — Builder LLM analyzes + calls tools via MCP)
   - Verify: `SELECT COUNT(*), kind, status FROM definition_proposals GROUP BY kind, status;` shows at least 1 row with `kind='agent', status='pending'`.
   - Paste the SQL output + the assistant_text from the API response.
5. `git diff --stat` + `git log --oneline -1` on `worker/cc19-builder-mcp-tools`. **Paste.**

---

## Hard constraints

- **Subscription-only invariant preserved.** The Builder must still run through claude-code by default. The Anthropic API key is NOT used by Builder in CC19's design (test_run is the only path that may fall back to it for sandbox execution — document this carefully).
- **No schema changes.** `definition_proposals` already supports `proposed_by` and `builder_session_id` (verify in models.py — should be present from CC18 era). Migration 0047 was the last one; CC19 does not add 0048.
- **citations validation stays strict.** `builder_propose` rejects citations with fabricated run_ids. Same protection as the current in-process `_propose`.
- **Don't break the in-process Builder tool registry.** When future briefs route Builder through Anthropic/Gemini/etc (provider-swap), the in-process implementations are still needed. Leave them as dead code for the claude-code path but functional for other adapters.
- **MCP server stays a single binary.** Don't fork a separate MCP server for Builder. Add the Builder tools to the existing one with conditional scoping.
- **Streaming endpoint not in scope.** `/messages/stream` SSE path stays text-only for CC19. Document as known-limitation. Streaming + tools is a separate brief (matches the responsiveness Phase 1 work).
- **Local-only git.** Worker commits on `worker/cc19-builder-mcp-tools`; terminal-Lead merges after Lead approves.

---

## Architecture decision documented

This brief implements **Option B (Builder uses claude-code's MCP path)** chosen over Option A (Anthropic-first cascade) and Option C (hybrid). Rationale per `docs/hollowness-audit-2026-05-29.md` "UPDATE — manual smoke complete, Layer 4 root-cause identified":

- Long-term: as personal-instance Artemis ships to multiple Amira employees, marginal token cost matters. Subscription model = flat cost regardless of adoption.
- The MCP infrastructure becomes the universal tool-execution path for the platform. Builder, Floating Artemis, Pipeline AI Panel all inherit it later.
- Provider-switch remains seamless for anthropic/gemini/openai/openrouter (their `.complete()` already handles tools); claude-code is the structural special case CC19 closes.

---

## Report-back format

```
CC19 — Builder MCP Tool Execution report
1. Commit / branch / worktree
2. LOC diff stats (per file)
3. Tests added + pass count
4. check.sh summary
5. Test_run recursion handling (which adapters tested as fallback)
6. End-to-end smoke output (PASTE the definition_proposals row that landed)
7. Anything surprising — especially around MCP scoping contextvar passing or claude-code subprocess lifecycle
```

---

**Worker: this brief closes the 4th and final layer of self-improvement hollowness. Layers 1-3 were producer-side (CC10-CC18). Layer 4 is consumer-side: the Builder LLM is capable but tool-less. After CC19, the loop closes end-to-end for the first time in the platform's history. The follow-on briefs (memory M1, Skills audit, Writing Studio integration) all depend on this loop being live.**
