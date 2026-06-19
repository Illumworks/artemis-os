# Brief — Screen-Time Watch #1: Pipeline + isolated data + tunable stance

**Owner:** app-seat Lead (me) → Sonnet worker(s) in isolated worktrees.
**Read first:** `docs/screentime-watch-plan.md`. **Coordination:** see
`../claudeck-artemis/COORDINATION.md` — **migration `0102` is claimed for this**;
own test DB `artemis_test_screentime`; stay disjoint from Forge's `dev_projects/*`.

**Goal:** the national Screen-Time Watch engine — isolated, scrubbable data + a
separate cron pipeline that fans out the existing scouts (nationally, screen-time
tuned), filters to "real moves," classifies each item's stance (config-driven) and
Amira angle, stores it, and recomputes per-state stance. (Callie reporting = Brief 2;
the page = Brief 3.)

## Scope

1. **Isolated data (own namespace; migration `0102`):**
   - `screentime_signals` (cols per the plan: state, level, district_name?, title,
     summary, status, stance, amira_angle, source_url, source_type, published_at,
     discovered_at, is_real_move, content_hash UNIQUE for dedup, raw jsonb).
   - `screentime_state_stance` (state PK, stance, rationale, signal_count, last_updated).
   - **Stance config** — tunable rules (a settings blob in `config.py` and/or a small
     `screentime_stance_config` row) the classifier reads. Default = the v1 definition
     in the plan. Editable WITHOUT a code change.
   - Do NOT reuse the marketing `SignalQueue` tables. New namespace only.
   - **Scrub:** add a repository function `purge_screentime_data()` (truncate the
     screentime_* tables) and support an optional **retention window** (auto-expire
     signals older than `screentime_retention_days`, default e.g. 60). Brief 3 wires
     the UI button; expose the function here.
   - Use migration `0102` (claimed). Before creating it run `alembic heads`; if 0102
     is already taken, STOP and re-coordinate (don't grab another silently).

2. **National scout fan-out (reuse, don't fork the scouts):**
   - Orchestrate the existing scouts under `artemis/scouts/` — `legislative`,
     `state_doe`, `board_minutes`, `regional_news` — configured for **all 50 states**
     and **screen-time** topics (query/keywords tuned to instructional screen-time
     limits + evidence-based-tool exemptions; NOT cellphone bans). Call them
     read-only — do NOT edit the scout modules (Forge/others may share them). If a
     scout needs screen-time params it doesn't expose, add a thin wrapper in
     `artemis/screentime/`, not an edit to the scout.
   - **National, not campaign-scoped:** do NOT inherit the campaign's target-state
     filter. Sweep the country.

3. **Process nodes:**
   - **Dedupe** (content_hash) + **"real moves" filter**: keep actual
     legislative/board actions (bill introduced/passed/amended, policy adopted, dept
     guidance); drop generic headlines/opinion. Make the bar explicit + testable.
   - **Stance-classify + Amira angle** per signal, **config-driven**, using a
     **tool-less provider (Codex → claude-code fallback; local LLM optional)** via
     `complete_with_fallback` (model inside the CompletionRequest, NOT as a kwarg —
     known footgun). This is bulk text work → keep it off Opus for cost.
   - **Store** signals; **recompute `screentime_state_stance`** per state from its
     signals (per the config rules).

4. **Pipeline registration (separate, on the pipelines page):**
   - Register this as its own pipeline in the pipelines system (`artemis/pipelines/`)
     so it shows on the pipelines page, isolated from the campaign pipeline — OR, if
     expressing scout fan-out as pipeline nodes is awkward, a dedicated cron runner
     under `artemis/screentime/` that still registers a pipeline entry for
     visibility. **Resolve this early** and note which you chose + why.
   - Cron trigger (config `screentime_cron`, e.g. daily). Rolling ~30-day window.
   - Failure-safe: a failing source never breaks the run; partial results still store.

## Constraints / coordination
- ORG RULE: no dependency added/upgraded <7 days old.
- Additive edits only to shared-risk files: `config.py` (settings), `main.py` (none
  expected in this brief — routes are Brief 3). Flag in COORDINATION.md before editing.
- Circular imports: lazy provider imports inside functions.
- Own test DB `artemis_test_screentime`; isolated worktree; heads-up before live restart.
- Cost: classification on Codex/local, never a national Opus sweep.

## Verification (observe the EFFECT)
- Migration `0102` applies clean; the screentime_* tables exist; campaign + memory
  tables untouched.
- A live (or fixtured) run stores real signals with stance + Amira angle; the
  per-state stance table is populated; a non-"real-move" headline is correctly dropped.
- Changing the stance config flips a signal's classification on re-run (proves tunable).
- `purge_screentime_data()` truncates only the screentime_* tables.
- A classification step is served by Codex/local (check serving provider), not Opus.
- `import artemis.main` clean; unit tests for the real-moves filter + stance config + dedupe + purge.

**Deliverable:** committed to a worktree branch; report migration number used, the
pipeline-vs-runner choice, the scouts wired + how national scope is set, the stance
config shape, and the live/fixtured run evidence (signals stored, state stance
computed, cheap provider used).
