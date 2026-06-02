# Brief Conventions

Conventions for writing implementation and audit briefs in this directory.

## Path conventions

**Implementation briefs** (Worker spawns with `isolation: "worktree"`) should use relative paths only. No `Repo: /Users/artemis/Desktop/Artemis/artemis-os` headers, no absolute `/Users/...` paths in file scope sections. This is defensive practice — the harness controls the worktree and Workers should write where they were spawned, not where a brief's header tells them to.

When a brief needs to reference a file outside the Worker's write scope (e.g. the frozen Node reference at `../claudeck-artemis/server/brief-generator.js`, or a sibling worktree to compare against), absolute paths are fine — but only for **read-only** files the Worker is told explicitly not to edit.

**Audit briefs** that span multiple repos (e.g. Codex audit briefs comparing the Python rebuild against the Node reference) may use absolute paths because the Worker is operating across two repo roots and there's no isolation flag involved. The same paths are also fine in design docs and decision records.

## CWD trap after background Agent completes (the real lesson from sessions 2026-05-18 → 19)

**When a background `Agent({isolation: "worktree"})` completes, the bash tool's CWD silently follows the harness into `.claude/worktrees/agent-<id>/`. Every subsequent `git log`, `git status`, `git rev-parse HEAD`, `git merge` runs against the worker's worktree — same branch name, same recent SHA. It looks identical to "the worker committed to lead." It is not. Lead's branch hasn't moved.**

This was misdiagnosed for the entire 2026-05-18 session as "J7 Worker bypassed isolation by reading absolute paths in its brief." That was wrong. J7 behaved correctly. The lead-side `git log` lying about HEAD is what fooled the diagnosis.

### The defensive reflex (mandatory)

After any background `Agent({isolation: "worktree"})` completes, **before trusting any git output**:

```bash
pwd
# or
git rev-parse --show-toplevel
```

If the result is under `.claude/worktrees/agent-<hash>/`, `cd` back to the main worktree explicitly before running merges or verifying state:

```bash
cd /Users/artemis/Desktop/Artemis/artemis-os   # or wherever lead lives
```

### How the trap manifests

- `git log --oneline -5` from inside the worker's worktree shows the worker's commits at HEAD on its branch. Looks like the merge happened.
- `git merge worker/<branch>` returns "Already up to date" because the current ref already contains those commits — but you're not on lead's ref.
- Bash tool calls from the harness inherit CWD across turns, so the trap persists until you explicitly `cd`.

### How J6e and J9 surfaced the trap

J9's `git log` claimed parent `1d375b8` (skipping J6e's merge that "should" have been the parent). That mismatch is what made terminal-Lead check `pwd` and find the CWD inside `.claude/worktrees/agent-ae52e47af1cb3813b/`. Lead's branch had been at `1d375b8` for the entire J6e/J9 cycle. The J6c/J6d/J7/J8 merges from the prior session were real because they happened from the actual main worktree before any worker isolation tripped the CWD.

### Relationship to the path convention above

The relative-paths convention still holds as defensive practice — Workers given absolute paths could theoretically still write to the wrong worktree, and the convention costs nothing. But the real load-bearing rule for not losing merges is the `pwd` reflex.

## Model + reasoning-effort tiering (Codex / Worker dispatch)

**Every brief states a recommended model + reasoning effort near the top** (a `**Recommended Codex model / effort:**` line under `Paste-into:`). The Lead picks the tier; the operator shouldn't have to guess and shouldn't burn the flagship on mechanical work.

The rule: **match the tier to the reasoning the task needs, not its importance.** A fully-specified brief (exact schema, exact signatures, exact tests) is execution, not reasoning — it goes in the cheap lane. Save the flagship for work where the agent must *figure something out*.

| Task shape | Codex model | Effort |
|---|---|---|
| Fully-specified brief (schema + pure fn + exact tests; mock removal; mechanical fix) | `gpt-5.4-mini` | low |
| Normal feature work, some inference needed | `gpt-5.4-mini` or `gpt-5.4` | medium |
| Ambiguous debugging, architecture, "figure out why X breaks" | `gpt-5.4` | high/xhigh |

A tight brief pays off twice — once in correctness, once in token cost (the more the Lead front-loads into the brief, the smaller the model that can execute it). Set a persistent `[profiles.worker]` in `~/.codex/config.toml` (`model = "gpt-5.4-mini"`, `model_reasoning_effort = "low"`) for the mechanical lane; CLI flags override per-run. (Model names current as of 2026-05; re-verify against `codex` docs if they've moved.)

## Concurrent Codex runs need separate worktrees (lesson, 2026-05-31)

**Two Codex accounts pointed at the same repo is NOT isolation — it's two cooks on one cutting board.** Codex (unlike the Agent tool with `isolation: "worktree"`) operates directly in whatever working tree it's launched in, creating + checking out branches in that shared tree. Run two concurrently in the same checkout and they fight over the single working tree + HEAD.

This bit us on the DIST1 ∥ pipe6 parallel fire: file-overlap analysis said "safe" (DIST1 backend, pipe6 frontend — no shared files), and that was necessary but **not sufficient**. They didn't collide on files; they collided on the tree. The pipe6 Codex branched *on top of* DIST1's commit (because DIST1's branch was checked out when pipe6 started), and the main repo ended up on the pipe6 branch while it ran — so the Lead couldn't merge DIST1 without yanking the tree out from under the live pipe6 run. (It resolved cleanly because pipe6 was still in its inventory phase and hadn't committed, but that was luck, not design.)

**The rule:** for genuinely parallel Codex runs, give each its own git worktree (or clone):
```bash
git worktree add ../artemis-<scope> lead/<integration-branch>
# launch the second Codex with cwd = ../artemis-<scope>
```
Then each run has its own tree + HEAD, branches independently off the integration branch, and the Lead merges each branch on its own. If you can't isolate, **run them sequentially** — finish + merge one before firing the next.

Two-sufficiency check before any parallel fire: (1) no file overlap, AND (2) no shared working tree. Both must hold.

**Env-var split for workers (lesson, 2026-05-31):** alembic's env reads `ARTEMIS_DB_URL`; pytest conftests read `ARTEMIS_TEST_DB_URL`. A worker prompt that says `ARTEMIS_TEST_DB_URL=... uv run alembic upgrade head` migrates the WRONG database (or silently no-ops against prod). Always pass BOTH in worker/dispatch prompts, split by tool:
```
ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/<db> uv run alembic upgrade head
ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/<db> uv run pytest <files>
```
Worktrees have no `.env`, so workers fall through to defaults unless these are explicit — making the wrong-DB trap silent.

## Other conventions

- Brief filenames: `<phase-letter><sub-number>-<kebab-summary>.md` (`j7-daily-brief-port.md`, `j6c-meetings-rebuild.md`).
- Briefs scope ≤ 500 LOC of implementation work. Larger surfaces decompose into letters (j6a, j6b, j6c, j6d).
- Each brief ends with a quality-acceptance checklist with explicit boxes. Workers tick every box with verbatim evidence in their final report.
- Acceptance: tests cover happy + failure modes; manual smoke pasted verbatim; diff re-read twice; migrations round-trip.
- A brief should be readable cold by an agent with zero session context. Test by handing it to a fresh agent and asking it to summarize what's being built.
