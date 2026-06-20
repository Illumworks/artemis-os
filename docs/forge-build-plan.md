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

### Phase 2 — Ares drives the full build loop (the experience)
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
