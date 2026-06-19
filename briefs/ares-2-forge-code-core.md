# Brief — Ares #2: Forge Code core (multi-session coding + the "one brain")

**Owner:** terminal opus (Lead). **Read first:** `docs/ares-architecture.md`
(Decisions 1 & 3), `docs/ares-plan.md` (P0/P1). **Depends on:** Brief 1 (Ares exists).

**Goal:** the Claude-Code-clone experience, under Ares, in Forge — **multiple
task-scoped sessions** backed by a **shared project-workspace memory** (the "one
brain"). Reuse the existing dev_projects loop; don't rebuild a code runner.

## Scope

1. **Sessions = many, brain = one.** Reuse `dev_sessions`/`dev_messages`
   (`artemis/dev_projects/models.py`) as the per-session model — they already carry
   provider/model/bypass_permissions/pinned/fork_of. Confirm create/list/resume/fork
   work from the Forge UI under the owner gate. Do NOT collapse to one session.

2. **Project-workspace memory** (the shared brain across a project's sessions):
   - Add a durable per-project drawer: a `project_workspace_memory` table (or a
     JSONB column on the project) holding `{plan, decisions, file_map, progress,
     open_threads}`. Scope it `agent:ares` (owner-private). New migration
     (check `alembic heads` first; number sequentially).
   - **Auto-resume:** on session load, hydrate Ares's system context from the
     project drawer ("here's the plan/decisions/progress so far") so a new session
     in the same project inherits the brain. Hook in `loop_runner.py` before the turn.
   - **Auto-capture:** after meaningful turns (plan set, decision made, files
     changed), update the drawer. Keep it lossless/append where it matters; the
     newest state wins on read (mirror the memory-observation conventions).

3. **Wire Ares as the driver of the dev_projects loop** (`loop_runner.py`):
   - Ares's persona + tools drive the loop; the existing `_maybe_run_local_tool`
     bash/file path is the code-running substrate. Surface the coding actions Ares
     needs (read/edit files, run build/test, git status/diff/commit-on-branch) as
     proper **tools he CALLS** (tool-calling discipline — never narrate). Respect
     `_DISALLOWED_BUILTINS` (Artemis tools, not claude-code CLI built-ins).
   - **Autonomy boundary (Decision 3):** autonomous read/edit/build/test/commit **on
     an isolated git worktree/branch**; **agency-gate** (propose→Jon confirms) any
     `git push`, merge-to-main, deploy, prod, or outward action. Never touch the
     shared main working tree directly.

4. **Build Reports (proactivity):** when Ares finishes a unit of work or hits a
   blocker, he DMs a concise Build Report / blocker to Jon (his own pings; he does
   NOT bypass notification silence — only Artemis does). Reuse the proactivity/Slack
   send path.

## Constraints / gotchas
- Provider for THIS brief stays the default (claude-code/Ares); cost-routing of
  sub-tasks is Brief 3 — don't pre-wire codex/local here.
- Circular imports: lazy provider imports. Migration numbering. Restart `-k` + verify pid.
- Worktree isolation for Ares's edits (and for any Sonnet workers you spawn to build this).

## Verification (observe the EFFECT)
- Two sessions in one project: a decision/plan captured in session A is visible to
  Ares in a fresh session B (the brain carries) — prove by a real round-trip.
- Ares makes a real edit + commit on an isolated branch autonomously; a `git push`
  attempt PROPOSES and waits for Jon's "yes" (does not auto-push).
- A finished task produces a Build Report DM.
- Unit tests for the project-memory persist/hydrate + the autonomy gate (push is gated).
- `import artemis.main` clean; live smoke before "done".

**Deliverable:** committed; report the memory schema + migration number, the
hydrate/capture hook points, the coding tools surfaced, the autonomy-gate proof,
and the live two-session "one brain" round-trip result.
