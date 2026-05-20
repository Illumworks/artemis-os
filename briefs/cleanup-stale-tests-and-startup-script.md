# Cleanup — Stale Tests + start-app.sh Hardcoded Path

**Owner:** Codex (paste-ready, mechanical-only)
**Branch:** `codex/cleanup-stale-tests-and-startup`
**LOC budget:** ~80 (full-diff insertions; cap at 100)
**STOP CONDITION:** if you reach 80 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** nothing. Independent cleanup.
**Grounded in:** `audits/operations-gap-report-v2.md` verification note ("104 passed / 2 failed"), task #16 in the running tracker.

## Why this brief exists

Three loose-end items have been pending across the last several sessions:

1. **Slack permalink test** expects `slack.com` but code now returns `amiralearning.slack.com` (deliberate fix in commit `5b546dc`). Test was never updated.
2. **Migration test** references a `.claude/worktrees/...` path that doesn't exist in the current layout — likely an orphan from a worktree-based test fixture that was removed.
3. **`scripts/start-app.sh`** has a hardcoded absolute path that breaks if anyone clones this repo to a different location.

None of these block anything, but they cause noise on every test run and friction on every fresh clone. OP-cleanup ships all three in one tiny brief.

## Scope

### In scope

1. **Update Slack permalink test expectation.**
   - File: `tests/` (locate via `grep -rn "slack.com/archives" tests/` — the test asserting the old expectation).
   - Change: assertion expects `amiralearning.slack.com` instead of `slack.com`.
   - Rationale: code change in `5b546dc` was intentional (workspace-direct permalinks avoid the DM-redirect-junk-params bug). The test should match the new contract.
   - Expected diff: 1-3 line change.

2. **Fix migration test path reference.**
   - File: `tests/` (locate via `grep -rn ".claude/worktrees" tests/` or similar).
   - Change: either delete the obsolete fixture/assertion if the worktree fixture is gone, or update the path to point at a stable location (e.g., a `tmp_path` fixture).
   - If the test is fundamentally obsolete (it was specific to a removed feature), delete it. Flag for Lead before deleting if uncertain.
   - Expected diff: 1-10 lines (delete OR rewrite, not both).

3. **Fix `scripts/start-app.sh` hardcoded path.**
   - Read the script. The hardcoded path is likely `/Users/artemis/Desktop/Artemis/artemis-os` or similar absolute reference.
   - Replace with `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` + `REPO_ROOT="$(dirname "$SCRIPT_DIR")"` pattern. Use `$REPO_ROOT` everywhere the absolute path was.
   - Verify the script still works when invoked from any CWD (test: `cd /tmp && /path/to/start-app.sh` — should behave identically).
   - Expected diff: 5-15 lines.

### Out of scope

- Any other test failures not in the two listed above.
- start-app.sh feature additions (logging changes, env loading rework, etc.).
- LaunchAgent / plist file changes. The TCC workaround stays as-is.

## Invariants

1. **No new test failures.** Run `./scripts/check.sh` after; the two specific failures should clear, no others should appear.
2. **start-app.sh remains POSIX-portable** (it's bash, not sh; keep `#!/usr/bin/env bash` shebang and bash-specific syntax). Don't introduce zsh-only or fish-only constructs.
3. **No new env vars introduced.** Path resolution is fully scripted.

## Files expected

- 2 test files updated (slack + migration). ~10 LOC delta total.
- `scripts/start-app.sh` updated. ~15 LOC delta.

Total: ~25 LOC. Well under cap.

## Test plan

1. **Slack permalink test passes** with new expectation.
2. **Migration test passes or is deleted** (whichever applies).
3. **`./scripts/check.sh` runs cleaner** — at least the two named failures should clear.
4. **Manual smoke: invoke `start-app.sh` from a different CWD** — `cd /tmp && bash /Users/artemis/Desktop/Artemis/artemis-os/scripts/start-app.sh --help` (or whatever non-destructive flag exists) — confirms path resolution works.

## What "done" looks like

1. 2 test changes + 1 script change committed.
2. The two named test failures clear.
3. start-app.sh works from any CWD.

## Report Codex submits

1. `git diff --stat` output.
2. The two specific test changes (paste before/after).
3. `./scripts/check.sh` exit status before/after.
4. Confirmation start-app.sh works from `/tmp` (just paste the resolved `$REPO_ROOT` value the script computes).
5. Branch.

---

**Lead notes (not for Codex):**
- This brief is small enough that Codex can ship it in one pass with no judgment calls. The only fork point is "delete or rewrite the migration test" — Codex picks based on whether the test seems to be exercising real behavior (rewrite) or just a vestigial fixture (delete). If it can't tell, flag for Lead.
