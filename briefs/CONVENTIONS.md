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

## Other conventions

- Brief filenames: `<phase-letter><sub-number>-<kebab-summary>.md` (`j7-daily-brief-port.md`, `j6c-meetings-rebuild.md`).
- Briefs scope ≤ 500 LOC of implementation work. Larger surfaces decompose into letters (j6a, j6b, j6c, j6d).
- Each brief ends with a quality-acceptance checklist with explicit boxes. Workers tick every box with verbatim evidence in their final report.
- Acceptance: tests cover happy + failure modes; manual smoke pasted verbatim; diff re-read twice; migrations round-trip.
- A brief should be readable cold by an agent with zero session context. Test by handing it to a fresh agent and asking it to summarize what's being built.
