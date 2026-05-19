# Brief Conventions

Conventions for writing implementation and audit briefs in this directory.

## Path conventions

**Implementation briefs** (Worker spawns with `isolation: "worktree"`) MUST use relative paths only. No `Repo: /Users/artemis/Desktop/Artemis/artemis-os` headers, no absolute `/Users/...` paths in file scope sections. Workers given absolute paths to the main worktree treat them as authoritative and write + commit there, defeating the isolation flag entirely.

**Precedent — J7 isolation leak (commit `6533323`, 2026-05-18):** the J7 Daily Brief Worker was launched with `isolation: "worktree"` and its own auto-created branch. Its brief (`briefs/j7-daily-brief-port.md`) opened with `Repo: /Users/artemis/Desktop/Artemis/artemis-os`. The Worker did all file work via absolute paths AND ran `git commit` from the main worktree — its commit landed on `lead/j6a-granola-integration` directly while its isolated branch ended the session with zero commits. The J6c, J6d, and J8 Workers in the same session respected isolation; their briefs used relative paths.

When a brief needs to reference a file outside the Worker's write scope (e.g. the frozen Node reference at `../claudeck-artemis/server/brief-generator.js`, or a sibling worktree to compare against), absolute paths are fine — but only for **read-only** files the Worker is told explicitly not to edit.

**Audit briefs** that span multiple repos (e.g. Codex audit briefs comparing the Python rebuild against the Node reference) may use absolute paths because the Worker is operating across two repo roots and there's no isolation flag involved. The same paths are also fine in design docs and decision records.

## Other conventions

- Brief filenames: `<phase-letter><sub-number>-<kebab-summary>.md` (`j7-daily-brief-port.md`, `j6c-meetings-rebuild.md`).
- Briefs scope ≤ 500 LOC of implementation work. Larger surfaces decompose into letters (j6a, j6b, j6c, j6d).
- Each brief ends with a quality-acceptance checklist with explicit boxes. Workers tick every box with verbatim evidence in their final report.
- Acceptance: tests cover happy + failure modes; manual smoke pasted verbatim; diff re-read twice; migrations round-trip.
- A brief should be readable cold by an agent with zero session context. Test by handing it to a fresh agent and asking it to summarize what's being built.
