# P2 — Tool Bridge + Reference Tool (signal_queue.write) + End-to-End Proof

**Paste-into:** terminal-Lead. It spawns a Claude Code Worker via `Agent({isolation:"worktree"})`.
**Target branch:** `worker/p2-tool-bridge`
**Browser smoke owner:** Lead (this session), post-merge
**Report back to me by:** Jon pastes terminal-Lead's relay (with Worker's full report) into Lead chat
**LOC cap:** 500 (full-diff insertions including tests). Hard stop at 600.
**Design reference:** `docs/tool-execution-architecture.md` — read it fully before starting. This brief implements the P2 portion of that design plus the `signal_queue.write` reference tool.

---

## Why this brief exists

Phase 1 made the LLM *see* rich instructions (persona, allowed reason codes, urgency discipline). But scouts still can't *act* — `run_agent()` calls `run_turn(tools=None)`. The tool-use loop in `artemis/agent/loop.py` already exists and works; it just has no tools registered. This brief builds the bridge from `agent.tools` (DB column of strings) to real `(Tool, ToolImpl)` pairs, and ships ONE reference tool — `signal_queue.write` — so we prove the whole loop end-to-end: scout LLM decides to write a signal → tool executes → row lands in `signal_queue`.

After P2 merges, a pipeline run produces real signals. That's the headline. P3 then adds the remaining tools following P2's established pattern.

---

## Scope

### Part A — Tool infrastructure (the contracts P3 will build against)

Implement exactly the shapes from `docs/tool-execution-architecture.md` "The bridge — what P2 builds":

1. **`artemis/tools/context.py`** — the `ToolContext` frozen dataclass: `session`, `agent_id`, `agent_db_id`, `agent_run_id`, `pipeline_run_id`. Verbatim from the design doc.

2. **`artemis/tools/registry.py`** — the `ToolFactory` type alias, module-level `_TOOL_FACTORIES` dict, `register_tool(name, factory)`, `get_factory(name)`, `known_tool_names()`. Verbatim from the design doc.

3. **`artemis/tools/__init__.py`** — imports every tool submodule so `register_tool()` side effects fire at package import. For P2, it imports just `artemis.tools.signal_queue` (P3 adds the rest).

### Part B — The executor bridge

Edit `artemis/builders/executor.py`. Replace the `tools=None` line and the "tool resolution is not yet implemented" warning block (currently ~line 181-189, 223) with the per-call registry assembly from the design doc's "Per-call registry assembly" section:

- Build a `ToolContext` from the session + agent + run_id + `shared_context.get("pipeline_run_id")`.
- For each name in `agent.tools` (strings; handle the dict shape defensively too), look up `get_factory(name)`, instantiate `(tool_def, impl)`, register into a per-call `ToolRegistry`.
- Unknown tool names: collect, log a single WARNING, skip them (don't crash).
- Pass `tools=tool_registry if len(tool_registry) > 0 else None` to `run_turn`.
- Add `import artemis.tools  # noqa: F401` at module top so factories register.

**Constraint:** do NOT change `run_agent`'s signature, the `_build_system_prompt` call (F2's work — leave it alone), or the cost-accounting block.

### Part C — Reference tool: signal_queue.write

**`artemis/tools/signal_queue.py`** — implement per the design doc's skeleton (the `signal_queue.write` example). Key requirements:

- `Tool` definition with the input_schema from the design doc (sourceType enum, headline, campaignFamily, urgencyTier, reasonCodes, evidence, optional districtId/sourceUrl/whyFlagged).
- Anti-spoof: `scout_type` / `discoveredBy` inferred from `ctx.agent_id`, not LLM input. Route through `normalize_intake_payload(arguments, scout_type=slug)`.
- **Reason-code allowlist enforcement** (design Q2 decision — both layers): before writing, check `arguments["reasonCodes"]` is a subset of the scout's allowlist from `josh_spec.reason_codes_for_scout(parse_spec(), slug)`. If any code is outside the allowlist, return `"VALIDATION_ERROR: reason code <X> not in this scout's allowlist [...]"` — do NOT write. The LLM can retry.
- **Write permission** (design "Permissions" section): only `marketing.scout.*` agents may call this tool. If `ctx.agent_id` isn't a scout, return `"PERMISSION_DENIED: agent <id> cannot write signals"`.
- On success: insert `SignalQueue` row with `signal_status="pending_qualification"`, `pipeline_run_id=ctx.pipeline_run_id`, `provenance={agent_run_id, agent_id, why_flagged}`. `await ctx.session.flush()`. Return `json.dumps({"signal_id": row.id, "status": "written"})`.
- On `normalize_intake_payload` ValueError: return `"VALIDATION_ERROR: <msg>"` (don't raise — the LLM reads the error and can retry).

### Part D — Tests

**`artemis/tools/tests/test_tool_bridge.py`** (the bridge):
1. Register a fake tool via `register_tool`. Build an agent with `tools=["fake.tool"]`. Run `run_agent` with a `model_adapter` mock that emits a tool_use for `fake.tool`, then end_turn. Assert the fake tool's impl was called with the expected args.
2. Agent with `tools=["nonexistent.tool"]`. Assert run completes, WARNING logged, run_turn got a registry without that tool (or None).
3. Agent with `tools=[]`. Assert `tools=None` passed to run_turn (backward-compat).

**`artemis/tools/tests/test_signal_queue_tool.py`** (the reference tool):
4. Valid signal from a scout agent → row lands in `signal_queue` with `signal_status="pending_qualification"`, correct `provenance.agent_run_id`.
5. Anti-spoof: LLM payload claims `discoveredBy="someone_else"` → stored `discovered_by` is the agent's actual slug.
6. Reason code outside allowlist → returns `VALIDATION_ERROR`, no row written.
7. Non-scout agent (e.g. `marketing.qualifier.cross_reference`) calls the tool → `PERMISSION_DENIED`, no row written.
8. `normalize_intake_payload` validation failure (e.g. invalid sourceType) → `VALIDATION_ERROR`, no row written, no crash.

Use mock LLM adapters (the existing `FakeAdapter` pattern in `artemis/agent/tests/` or `artemis/builders/tests/`). Use the real test DB (conftest hard-fail invariant in place).

### Part E — End-to-end proof (the headline)

A test in `artemis/tools/tests/test_e2e_scout_signal.py`:
- Seed/ensure `marketing.scout.regional_news` agent exists with `tools` including `signal_queue.write`.
- Mock the LLM adapter to: first turn → emit a `tool_use` for `signal_queue.write` with a valid signal (sourceType=news_article, a valid regional_news reason code like VENDOR_DISSATISFACTION, etc.); second turn → end_turn with a summary.
- Run `run_agent(session, "marketing.scout.regional_news", shared_context={"pipeline_run_id": "e2e-test"})`.
- Assert: `signal_queue` has exactly one new row with `discovered_by="regional_news_scout"` (or the slug), `pipeline_run_id="e2e-test"`, `provenance.agent_run_id` set.

This proves the full loop with mock LLM. The REAL-LLM smoke (actual claude-code call) is Lead's post-merge browser/pipeline smoke.

---

## Files owned by this stream

- NEW: `artemis/tools/__init__.py`
- NEW: `artemis/tools/context.py`
- NEW: `artemis/tools/registry.py`
- NEW: `artemis/tools/signal_queue.py`
- NEW: `artemis/tools/tests/__init__.py`
- NEW: `artemis/tools/tests/test_tool_bridge.py`
- NEW: `artemis/tools/tests/test_signal_queue_tool.py`
- NEW: `artemis/tools/tests/test_e2e_scout_signal.py`
- EDIT: `artemis/builders/executor.py` (the bridge wire-up only — do not touch `_build_system_prompt`)

**Do not touch any other files.** Especially:
- `artemis/marketing/josh_spec.py` (F1, sealed — import + call only)
- `artemis/agent/loop.py`, `tools.py`, `types.py` (the loop already works — use it, don't modify it)
- Any agent blueprint markdown (P1/P4 streams)
- `artemis/marketing/scout_runner.py` (legacy path — not part of this bridge)

---

## Acceptance criteria (Worker must demonstrate each)

1. `uv run pytest artemis/tools/tests/ -v` — all tests pass (8 unit + e2e). **Paste the summary.**
2. **Bridge smoke:** `uv run python -c "import artemis.tools; from artemis.tools.registry import known_tool_names; print(known_tool_names())"` shows `('signal_queue.write',)`. **Paste output.**
3. **E2E test passes** (the headline). **Paste the specific test line.**
4. `./scripts/check.sh` passes modulo the known pre-existing j5b Jira flake (and any b3 event-loop isolation flakes — verify those pass in isolation if they appear). **Paste the final summary + your flake verification.**
5. `git diff --stat` — full-diff insertions ≤ 500 (600 hard stop). **Paste it.**
6. `git log --oneline -1` on `worker/p2-tool-bridge`. **Paste it.**

---

## Hard constraints

- LOC cap: 500 (600 hard stop). At cap, commit what's done, ping back.
- Do not modify the agent loop (`artemis/agent/*`). It already works.
- Do not modify `_build_system_prompt` or `run_agent`'s signature.
- Do not implement tools other than `signal_queue.write` — that's P3.
- Do not run a DB re-seed.
- Local-only git. No `git push`. Worker commits on `worker/p2-tool-bridge`; terminal-Lead merges after Lead approves.

---

## Report-back format (Worker pastes verbatim, filled in)

```
P2 — Tool Bridge report

1. Commit hash:            <git log -1 --format=%H on worker/p2-tool-bridge>
2. Branch / worktree:      worker/p2-tool-bridge / <path>
3. LOC diff stats:         <git diff --stat against fork point>
4. Files changed:          <numbered list>
5. Test pass:              <pytest summary line>
6. Bridge smoke:           <known_tool_names() output>
7. E2E test result:        <the e2e test pass line>
8. check.sh:               <final summary + flake verification>
9. Anything surprising:    <free text — especially any place the F4 design didn't match reality>
```

---

**End of brief. Claude Code Worker: read `docs/tool-execution-architecture.md` fully first, then this brief. Operating principle: never assume — if the F4 design conflicts with what's actually in `artemis/agent/loop.py`, STOP and report the conflict to Lead before improvising. The design was written from a read of the loop, but verify it against reality.**
