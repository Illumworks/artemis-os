# Forge / Ares — Build Plan + Context (executor reference)

> Companion to `docs/forge-vision.md` (the WHY/strategy north star). This doc is
> the HOW: consolidated context + a phased, delegation-ready build plan. Workers
> pick up chunks from here. All file:line refs were accurate 2026-06-19 — VERIFY
> at build time, they drift.
>
> Status: PLAN (awaiting Jon's review). Green light to build follows review.
> Delegation is mandatory (see "Delegation model").

## 0. What we're building (one paragraph)

A full Claude Code experience living inside Artemis: a browser-based, always-on
build control room for an execution engine on the Mac mini. Fire a build from any
device's browser; it runs server-side (the mini has the local project folders),
survives the tab closing, and you reconnect to watch/review/approve from
anywhere. Durable per-project memory (the "one brain"), OS integration, and
cost-routing for free because execution runs inside Jon's system. Owner-private;
Ares is the driver. Desktop-first (full right rail); mobile later. Dual-track
on-ramp: claude.ai Code keeps maintaining Artemis itself until Forge earns it.

## 1. Context — current substrate (consolidated; verify at build time)

Stack: Python 3.11, FastAPI, SQLAlchemy 2.x async + asyncpg, Postgres 15 +
pgvector, Alembic, Anthropic SDK, `uv`. Local-only git. Owner-gated surface.

**The honest state:** the old "Dev Projects"/Forge surface is UI scaffolding with
a STUB engine. Audits: ~0 dev_messages ever. We extend the shell; we replace the
engine.

| Area | File(s) | State | Reuse vs build |
|---|---|---|---|
| Session model | `artemis/dev_projects/models.py` — `dev_projects`, `dev_sessions` (provider/model/bypass_permissions/pinned/fork_of), `dev_messages`, `dev_annotations` | exists; NO `agent_id` col | REUSE as-is (Forge=Ares for now) |
| One-brain memory | `artemis/dev_projects/models.py` `ProjectWorkspaceMemory` + `workspace_memory.py` + migration `0101` | **DONE 2026-06-19** (live + tested) | REUSE — wire hydrate/capture |
| Loop runner | `artemis/dev_projects/loop_runner.py` | STUB: `_maybe_run_local_tool` keyword bash heuristic; own `PendingPermission` gate; NO system prompt / agent_id / real tool loop | REPLACE engine; keep persist/broadcast |
| Routes + owner gate | `artemis/routes/dev_projects.py` (`Depends(require_owner)`); UI `public/js/features/dev_projects.js` | exists (create/list/resume/fork/send/files) | REUSE; add agent-loop send path |
| Tool registry + agency gate | `artemis/floating_artemis/{authority,tool_registry,chat}.py`, `/tool-confirm` route | mature: `AuthorizedToolRegistry`, layer 3/4 `ConfirmationStore` propose→confirm | REUSE — this is our autonomy gate |
| Ares identity | persona/scope/surface/tools (Brief 1) + `always_respond_in_channels` | **DONE + LIVE** | REUSE; extend tool set per phase |
| Core agent loop | `artemis/agent/loop.py` `run_turn` | exists (used by spawn_subagent) | REUSE as the turn engine |
| Durable runs | pipelines + `agent_runs` / `cost_events` job machinery | exists | REUSE for the build-run model |
| Worktree isolation | — | NONE (human/Claude-Code convention only) | BUILD: `artemis/dev_projects/worktree.py` |
| Build-report DM | `artemis/hub/notify.py` `notify_jon(requested_by="artemis")` | exists; sole-interrupt invariant | REUSE — Ares passes `requested_by="artemis"` |
| Provider | `"claude-code"` (model default + route + JS) | default | KEEP; codex/local routing is later |
| Migrations | alembic head = **0101** (after this session) | — | next = 0102 |

**The key architectural split (settled):** `loop_runner` and the floating_artemis
tool/agency path are disjoint today. DECISION: drive Forge turns through the agent
loop + the FA `AuthorizedToolRegistry`/`ConfirmationStore` (one agency gate, not
two), while keeping the dev_projects session store + UI + WS streaming. Reuse
loop_runner's shell exec as the command substrate.

## 2. Locked decisions

1. **Full Claude Code app in Artemis** (incl. desktop right rail). Extend the
   dev_projects shell; build the real engine; add review panels.
2. **Durable run model is the technical core** — a build is a server-side job
   (status/logs/reconnect), not a request-scoped background task. Reuse existing
   run/job machinery.
3. **One agency gate** — reuse FA layer-3 `ConfirmationStore` for propose→confirm;
   do NOT build a second gate in loop_runner.
4. **Autonomy:** Ares reads/edits/runs/tests/commits on his own isolated
   worktree/branch autonomously; GATE only push / merge-to-main / deploy / prod /
   spend.
5. **Worktree isolation is the safety story** — no edit tools until it exists.
6. **Owner-private; Ares drives Forge** (no per-session agent_id column yet).
7. **Provider stays claude-code**; cost-routing (codex/local) is a later phase.
8. **Desktop-first; mobile later** (where the right rail gets trimmed).

## 3. Phased plan (each chunk is worker-sized; verify the EFFECT, not HTTP 200)

### Phase 0 — Hardening (always-on bedrock; do FIRST)
- **0.1 Tunnel:** pin cloudflared to `127.0.0.1` (root cause: `service: localhost`
  resolves IPv6 `::1`, uvicorn binds IPv4 only → intermittent 502). Verify:
  sustained external requests to app.artemisos.me, zero 502 over a soak.
- **0.2 Freeze bug:** investigate the armed DIAG trap (grep `=== DIAG` in
  app.err.log); root-cause the asyncpg/loop freeze; bound timeouts. Verify: no
  freeze under load; DIAG clean. (Investigation-heavy — Opus-led + worker.)
- **0.3 Auto-recover (optional):** healthz watchdog → launchd restart on hang.

### Phase 1 — Durable run model (the core)
- **1.1** Build-run job: a Forge turn/build runs as a durable server-side job
  (reuse agent_runs/pipeline machinery) with status + appendable log.
- **1.2** Reconnect/stream: any device attaches to a live build, replays log on
  reconnect. Verify: start build → close browser → reopen on another device →
  still running + catches up.

### Phase 1 — DESIGN (locked 2026-06-19, from substrate research)

**Gap (precise):** a Forge turn is a detached `asyncio.create_task(run_turn(...))` (returns 202 immediately) — so it ALREADY survives the browser closing (it dies only if the process crashes). But streamed output (`dev_projects.token`) is broadcast ephemerally via `ws_manager` and never persisted; the assistant message is written to `dev_messages` only once, at end-of-turn. So a device reconnecting mid-build sees nothing of the in-flight work, and there is no "is a build running" signal. Reuse: `ws_manager` (room-keyed, in-proc), the `create_task` launch, and the `/ws/agent-runs/{run_id}` on-reconnect replay pattern (`_send_initial_agent_state`). `agent_runs` lacks a streamable log + a dev_session FK, so Forge gets its own run tables.

**Data model (new, migration 0102):**
- `forge_runs`: id PK, `run_id` (text, unique, WS-room key), `dev_session_id` FK->dev_sessions (indexed), `project_id` FK->dev_projects, `status` (running|completed|failed|cancelled), `started_at`, `completed_at?`, `error?`. One row per turn/build.
- `forge_run_log`: id PK, `run_id` FK->forge_runs.run_id (indexed), `seq` int (ordered), `kind` (token|message|tool_use|tool_result|permission|error|status), `payload` JSONB, `created_at`. APPEND-ONLY (lossless) — the replayable transcript. Index (run_id, seq).

**Reconnect contract:** `GET /api/dev-projects/sessions/{id}/active-run` -> `{run_id, status, log:[{seq,kind,payload}...]}` or `null`. Frontend calls it on session load; if a run is active, it renders the replayed log (reconstructs the in-flight streaming bubble) then joins the live WS. Mirrors `_send_initial_agent_state`.

**Chunks:**
- **1.1 (data layer, independent — START FIRST):** forge_runs + forge_run_log models + migration 0102 + repository (`create_run`, `append_log(run_id, kind, payload)` with seq, `complete_run`, `get_active_run_for_session`, `get_run_log`) + round-trip tests. Mirrors the workspace_memory data-layer chunk.
- **1.2 (loop_runner integration, after 1.1):** in `run_turn`/`_run_provider_completion` create a forge_run on start, append each broadcast event to forge_run_log (BATCH token deltas — do NOT write one row per token; flush every ~50 tokens or ~400ms), complete the run at end. Keep existing ephemeral WS broadcast unchanged (log is additive). High-judgment — Lead reviews.
- **1.3 (reconnect/replay, after 1.1, parallel with 1.2):** the `/active-run` endpoint + log replay; touches routes/dev_projects.py.
- **1.4 (frontend, after 1.2/1.3 contract):** dev_projects.js shows "build running" + replays the log into the streaming bubble on load, then joins WS.

**Scope boundary (honest):** V1 runs are in-process detached tasks — they survive tab-close + reconnect, and the persisted log preserves everything up to a crash, but an in-flight turn does NOT survive an app restart (the readiness guard + watchdog keep the process alive). True cross-process durability (worker queue + Redis WS fanout) is a later phase — `ws_manager` is explicitly single-process.

### Phase 2 — DESIGN (locked 2026-06-20, from integration research)

**Decision: Option B** — keep `loop_runner.run_turn` as the orchestrator; inject Ares's
brain into it. (Option A = routing through `handle_turn` was rejected: different session
store (floating_artemis_messages vs dev_messages), different WS room (`fa:` vs
`dev-projects:`) + event shapes, and it bypasses Phase-1 forge_run logging — 100+ lines
of glue fighting the architecture.) Option B preserves dev_messages history, the existing
Forge WS streaming, and forge_run logging with ZERO frontend changes for the read-only core.

**Which sessions are Ares-driven:** ALL Forge sessions (owner-private; locked decision #6).
No agent_id column, no migration — loop_runner drives every claude-code Forge turn as Ares.

**Streaming model change (conscious tradeoff):** `agent/loop.run_turn` (the tool-capable
iterative loop, already used by spawn_subagent) takes a plain `ToolRegistry` + `system` +
`hooks` and runs COMPLETE-response iterations — it is NOT token-streaming like the current
`adapter.stream()` path. So Phase 2 replaces token-by-token streaming with STEP-level
streaming via the loop's `HookRegistry` (`on_message` fires per assistant message + per tool
result): each step is broadcast to the existing `dev-projects:{id}` WS room AND appended to
forge_run_log. This is the right signal for "watch Ares work" (read file X -> ran git_status
-> reasoning) and is REQUIRED to support tools at all. Token-streaming-within-each-step is a
possible later polish (would need a streaming tool-loop). Tool execution: wrap Ares's
`AuthorizedToolRegistry` via the `_build_auto_invoke_tool_registry` pattern (layer 1/2 auto;
layer 3 staged) into the plain ToolRegistry the loop consumes. Provider stays claude-code.

**Project-path scoping (the one genuinely new thing):** Brief-1's `read_file` is constrained
to the artemis-os repo root. Forge coding tools must be scoped to the SESSION's project root
(`DevProject.path`), injected at registry-build time — so a factory `_make_read_file(root)` /
`_make_list_dir(root)` etc., closed over `project_path`, mirroring `_make_query_memory(agent_id)`.

**Chunks (thin-magic-slice order; right rail is separable, comes last):**
- **2.1 Ares coding tools (self-contained, START FIRST):** in `tools/core.py` add layer-1
  read-only, path-constrained factories `_make_read_file(root)`, `_make_list_dir(root)`,
  `_make_git_status(root)`, `_make_git_diff(root)`; extend `_build_ares_tool_registry(agent_id,
  project_path=None)` to register them when project_path is given. Tests. No loop_runner touch.
- **2.2 loop_runner Ares integration (the core; Lead reviews hard + live-tests):** rewrite the
  claude-code path in `_run_provider_completion` to build Ares system prompt + Ares registry
  (with project_path) wrapped to a ToolRegistry, call `agent.loop.run_turn` with a HookRegistry
  that bridges each step to the Forge WS (dev_projects.message + a tool-step event) AND
  forge_run_log. Keep dev_messages persistence + Phase-1 run lifecycle. Depends on 2.1.
- **2.3 frontend tool-step rendering (small):** dev_projects.js renders tool-step events
  ("Ares ran git_status" + collapsible output). Depends on 2.2 event shape.
- **2.4 right rail (separate, larger — AFTER the core slice proves out):** file tree + diff
  viewer + plan/todo panel wired to live build state. Desktop. The thin magic slice (2.1-2.3)
  = "Ares answers in Forge in his voice, reads your project, you watch him work" ships first.

### Phase 2 — DECISION (Jon, 2026-06-20): Real Claude Code on Max subscription

Live testing revealed claude-code runs tools in a black-box subprocess (no per-step
events, no in-process tools reach it) while the anthropic API path gives step-streaming
+ our tools but is metered $$. **Jon chose: claude-code engine (Max subscription)** —
Forge wraps the REAL Claude Code running in the project dir with its native file/bash/git
tools. Cheap, ideal for long unattended builds, IS the tool Jon wants. Tradeoff accepted:
no live per-step "watch it work" for now (see result + summary; text-progress streaming is
a later enhancement, the adapter's "CC19" SSE limitation).

**Consequences / re-plan:**
- Chunks 2.1 (project tools), 2.2 (hook step-streaming), 2.3 (tool-step UI) are PARKED —
  built for the API path, not deleted, reusable if we ever add the hybrid. The claude-code
  Forge turn currently fails safe to bare-completion; superseded by the mode below.
- **NEW Phase 2 (claude-code Forge mode):** a Forge adapter mode that runs `claude -p` with
  cwd=project.path and its NATIVE coding tools allowed (currently the adapter does
  `--disallowed-tools Bash Read Write Edit`; Forge mode must allow them), sets the required
  session contextvar, returns the final text, and logs it to forge_run + persists to
  dev_messages. Ares persona via system/append. Provider stays claude-code (Max).
- **SAFETY MODEL SHIFT (important):** since tools run INSIDE the subprocess we CANNOT gate
  individual tool calls (no per-call layer-3 confirm). Safety therefore comes from:
  (a) running in an ISOLATED git worktree/branch (Phase 3 — now the PRIMARY safety boundary),
  (b) no push / no network egress, (c) human reviews the diff before merge. So Phase 3
  (worktree isolation) is now load-bearing and should gate before edits are enabled. For the
  immediate slice, can start read-leaning (let Claude Code read+answer in the project) and
  enable edits once worktree isolation lands.
- "Watch it work" later = stream the subprocess's text/stdout progress (separate brief).

### Phase 2 — BUILD (the experience)
- **2.1** Route Forge `send_message` through the agent loop with Ares persona +
  system prompt + FA tool registry; hydrate one-brain on start. Keep dev_messages
  persist + WS stream.
- **2.2** Read-only coding tools first: read_file/list_dir/git_status/git_diff/
  read-only run. Stream tool-call steps to the UI ("watch it work").
- **2.3** Right-rail panels (desktop): file tree, diff viewer, plan/todo wired to
  live build state. Verify: open project, talk to Ares, watch step-by-step with
  memory, right rail populates.

### Phase 3 — Safe autonomous edits
- **3.1** `artemis/dev_projects/worktree.py`: git worktree add/remove per build,
  branch isolation, link to dev_session.
- **3.2** edit/write/run/test/commit tools (layer 1/2 auto) IN the worktree;
  git push/merge as layer-3 GATED (propose→confirm). Never touch shared main tree.
- **3.3** Review/approve UX: diff review + approve-merge from the UI. Verify: Ares
  edits+commits autonomously on an isolated branch; a push PROPOSES and waits;
  throwaway branch leaves real code untouched.

### Phase 4 — One-brain into builds (data layer DONE)
- **4.1** hydrate-on-start + capture tools Ares CALLS (record_decision/
  update_progress/set_plan/update_file_map; lossless decisions). Verify: decision
  in session A is visible to Ares in fresh session B (real round-trip).

### Phase 5 — Build Reports
- **5.1** on finish/blocker → Ares DMs a Build Report via
  `notify_jon(requested_by="artemis")` (Ares does NOT bypass silence). Verify:
  finished task → DM lands.

### Phase 6 — Mobile pared-down view (later, if beneficial).

## 4. Delegation model (mandatory)

- **Opus (Lead):** plans, decomposes into chunks, designs the risky integration
  points, reviews every diff, merges, and runs the LIVE smoke. Security/scope and
  architecture judgment stay here.
- **Sonnet workers:** build chunks in ISOLATED git worktrees on `worker/forge-*`
  branches; file-disjoint within a phase so merges don't collide. Each gets a
  tight brief + the relevant context from this doc.
- **Codex / 2nd Claude Max:** alternative executors for parallel streams.
- Worker briefs use repo-RELATIVE paths (absolute paths defeat worktree
  isolation). Workers can't run DB tests/alembic (no .env in worktree) — Lead runs
  migrations + DB tests + live smoke on main.
- Never declare done without observing the EFFECT (real build, real DM, real
  reconnect), not just passing tests.

## 5. Cross-cutting gotchas (every chunk respects)

- Org/CLAUDE rule: no dependency <7 days old; commit lockfiles.
- Migrations: check `alembic heads`; number sequentially; run `alembic upgrade
  head` on live (+ the test DB) after merge.
- Circular imports: lazy provider imports inside functions, never at module top.
- JSONB in-place mutation needs `flag_modified` or the UPDATE is dropped.
- Restart: `launchctl kickstart -k gui/$(id -u)/me.artemisos.app` then VERIFY pid
  changed + `/healthz` 200.
- Lossless memory rule: decisions/evidence append-only; no delete APIs.
- ASCII only in personas/voice corpus. Crypto = `integrations.crypto` (bytes).
- Tunnel/freeze are PREREQS — long unattended builds need a rock-solid mini.

## 6. Open questions (for Jon)
- First real "other project" to test the engine on?
- Hardening strictly first, or Phase 0 in parallel with the Phase 1 durable-run
  slice?
- Confirm the thin-magic-slice discipline: get one end-to-end build working
  (browser → mini → result → approve) before investing in right-rail breadth.

### Phase 2 — BUILD STAGING (claude-code Forge mode, 2026-06-20)

Research gave exact adapter edit points. Two real blockers: the claude CLI canNOT block
network if Bash is enabled (curl/wget work; needs OS sandbox later), and --add-dir behavior
under bypassPermissions is unverified. So we stage safety-first:

- **F1 (adapter Forge mode, OPT-IN):** new `forge_project_path_var` contextvar
  (artemis/dev_projects/context.py). New `_build_forge_command` in the claude-code adapter:
  `claude -p --output-format json --model ... --add-dir <project> --permission-mode
  bypassPermissions` with READ-ONLY native tools allowed (Read, Glob, Grep), WebSearch/
  WebFetch/Bash/Write/Edit DISALLOWED for this first slice. `_run_subprocess` gains
  cwd=project_path. `_complete_with_tools` uses the forge command ONLY when the contextvar
  is set (existing MCP path untouched).
- **F2 (loop_runner wiring):** for claude-code Ares Forge turns, set forge_project_path_var
  (+ floating_session_id_var) around the adapter call; drop the parked hook/in-process-tool
  path; log final result to forge_run + persist to dev_messages. Ares persona via system.
- **F3 (Phase 3 gate):** enable Write/Edit/Bash ONLY inside an isolated git worktree
  (worktree isolation = the primary safety boundary). Accept the network caveat or add
  macOS sandbox-exec later. Edits do NOT ship until the worktree boundary lands.
- **Later:** stream subprocess stdout (--output-format stream-json) for live "watch it work".
