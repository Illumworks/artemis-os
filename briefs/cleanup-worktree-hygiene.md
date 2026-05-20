# Cleanup — Worktree Hygiene + Operational Discipline

**Owner:** terminal-Lead (NOT Codex — needs branch judgment + git operations on multiple worktrees)
**Branch:** `lead/j6a-granola-integration` (no new branch; operations span existing ones)
**LOC budget:** ~30 (mostly just `git worktree remove` commands + an `.gitignore` adjustment if anything)
**Brief author:** Lead (Opus 4.7)
**Depends on:** M3a in flight (must NOT remove that worktree). Other merges (mem-m2, M7, ruff baseline, ruff format) ideally landed before this runs.

## Why this brief exists

Three structural problems with the current worktree state, all rooted in the same cause: **Sonnet Worker worktrees in `.claude/worktrees/` and sibling `artemis-os-*` directories accumulate after their work merges, and ruff/grep/etc. scan them.**

1. **Ruff noise** — 118 of 137 errors before the cleanup brief excluded `.claude/worktrees/`. The exclusion is now in `pyproject.toml`; the directories themselves are still consuming disk + confusing tools.
2. **Branch-slip pattern** — at least 4 branch slips this session. When a Worker's worktree sits in the main repo's `.claude/worktrees/` subdirectory or as a sibling `artemis-os-*` dir, the parent worktree's CWD/branch state can drift unexpectedly. Removing the stale ones reduces surface area.
3. **Mental load** — `git worktree list` returns 16 entries. Half are dead. Operators can't tell at a glance which are active.

After this brief: only ACTIVE worktrees exist; `git worktree list` is a clean signal; `git branch` lists only branches that are mid-flight or merged-but-historical.

## Scope

### In scope

1. **Inventory active vs stale.** Run `git worktree list` and classify each:
   - **ACTIVE** — work in flight, do NOT touch:
     - `/Users/artemis/Desktop/Artemis/artemis-os` (main — terminal-Lead + Codex shared)
     - `/private/tmp/artemis-os-mypy-clean` (Codex's mypy in-flight clean room)
     - `.claude/worktrees/agent-a9a5fe0e52a4c784c` (worker/m3a-state-sweep — still running)
   - **CONFIRMED-MERGED, safe to remove:**
     - `.claude/worktrees/agent-adfc03419f931ebe5` → worker/m1-reason-code-registry (MERGED)
     - `.claude/worktrees/agent-a1269b570c380413a` → worker/m3-campaign-state-machine (MERGED)
     - `.claude/worktrees/agent-ac8f5021db0d68962` → worker/m7-writing-studio-overview (merging now)
     - `.claude/worktrees/agent-adb517409c1f18364` → worker/mem-m2-validity-and-conflicts (merging now)
     - `.claude/worktrees/agent-ad1ea1a50db0493f6` → worker/j10e-oauth-token-refresh (merged sessions ago)
     - `/Users/artemis/Desktop/Artemis/artemis-os-o5` → worker/o5-builder-nav-polish (MERGED)
   - **STALE UNNAMED (likely safe but verify):**
     - `.claude/worktrees/agent-a35de673bfcf25c6c` → branch `worktree-agent-a35de673bfcf25c6c` (no obvious work attached)
     - `.claude/worktrees/agent-a61c0d606416776e7` → branch `worktree-agent-a61c0d606416776e7`
   - **OLDER LEAD EXPERIMENTS (need Jon's confirmation before removing):**
     - `/Users/artemis/Desktop/Artemis/artemis-os-d4` → lead/provider-selector-ui
     - `/Users/artemis/Desktop/Artemis/artemis-os-dev-projects` → codex/dev-projects-rebuild
     - `/Users/artemis/Desktop/Artemis/artemis-os-lane-c` → lead/memory-inspector-wiring
     - `/Users/artemis/Desktop/Artemis/artemis-os-lead` → lead/swr-cache
     - `/Users/artemis/Desktop/Artemis/artemis-os-lead2` → lead/g1-floating-artemis-backend

2. **Remove confirmed-merged worktrees.** For each: `git worktree remove --force <path>` (force because they're locked). Verify the branch ref still exists (`git branch --list worker/m1-*` etc.) — the branch stays as historical reference; only the worktree directory goes.

3. **For each stale unnamed worktree:** verify the branch has no commits beyond `lead/j6a-granola-integration`'s history (`git log --oneline <branch> --not lead/j6a-granola-integration`). If empty, remove the worktree AND delete the branch. If non-empty, flag for Jon — those commits represent lost work.

4. **For the 5 older lead/codex experiments:** DO NOT auto-remove. List them in the report with their commit hashes and one-line description (`git log -1 --oneline <branch>`). Jon decides per-branch whether the work is still useful.

5. **Clean up `.clone/`** if it's a duplicate worktree shadow. Check `ls .clone/` — if it contains a stale `agent-*` or `worktrees/` sibling, remove. Don't touch unless empty after step 2 OR clearly a duplicate.

6. **Update `.gitignore`** to ensure `.claude/`, `.clone/`, `.sync/` are all ignored at the repo root (they should be, but verify — ruff exclude was added but git tracking is separate).

7. **Pruning step:** `git worktree prune --verbose` after all removals. Cleans up admin metadata for removed worktrees.

### Out of scope

- Removing the **lead/personality-voice-profile** branch (stash exists; user's WIP).
- Removing any branch with the `lead/` prefix without Jon's per-branch sign-off.
- Reorganizing the directory layout. Keep current convention.
- Forcing terminal-Lead and Codex to use separate worktrees. That's the long-term right answer but it's a workflow change, not a cleanup.

## Invariants

1. **Never remove the worktree for `worker/m3a-state-sweep`** — that Worker is paused on a checkpoint, not abandoned. Worker will resume after Lead decisions land.
2. **Never `git branch -D` a branch without first verifying it has no unmerged commits** vs lead. If it has commits, flag for Jon.
3. **`git worktree remove --force`** is acceptable since these are locked worker dirs. Standard `remove` will fail on locked.
4. **No `rm -rf`** anywhere. Use `git worktree remove` so git's metadata stays consistent.

## Files expected

- `.gitignore` — maybe 1-3 line addition if `.claude/` / `.clone/` / `.sync/` aren't already ignored.
- No code changes. This is filesystem + git plumbing.

Total: ~5 LOC plus the worktree removal commands (which don't count as LOC).

## Test plan

1. After cleanup: `git worktree list` shows only the ACTIVE list above (3 entries).
2. `git branch --list 'worker/*'` shows only `worker/m3a-state-sweep` plus optionally historical merged branches.
3. `du -sh .claude/worktrees/ .clone/ 2>/dev/null` returns small numbers (or "no such file").
4. `uv run ruff check` still returns 0 (regression check on the exclude).
5. `./scripts/check.sh` exit status unchanged from pre-cleanup.

## What "done" looks like

1. 6 confirmed-merged worktrees removed.
2. 2 stale unnamed worktrees either removed (if empty) or flagged.
3. 5 older lead/* experimental worktrees listed in the report with commit metadata for Jon's per-branch decision.
4. `.gitignore` verified to ignore `.claude/`, `.clone/`, `.sync/`.
5. `git worktree list` is signal, not noise.

## Report terminal-Lead submits

1. `git worktree list` BEFORE and AFTER (paste both).
2. List of branches removed (if any) with last-commit hash + date for record.
3. The 5 older lead/* experiments with `git log -1 --oneline <branch>` output — Jon decides per branch.
4. The 2 stale unnamed worktrees — were they empty (removed) or did they have work (flagged)?
5. `du -sh` before/after on `.claude/worktrees/` and `.clone/`.
6. Confirm `worker/m3a-state-sweep` worktree is STILL PRESENT (sanity check the active-Worker invariant).

---

**Lead notes (not for terminal-Lead):**
- This is hygiene, not strategy. Do it after the current merge wave (mem-m2 + M7 + ruff baseline + ruff format + mypy) clears, but before spawning M4/M5b/OP1 Workers. Clean substrate for the next wave.
- The 5 older `artemis-os-*` siblings (d4, dev-projects, lane-c, lead, lead2) are pre-this-session experiments. Some might be live exploration; some are abandoned. Jon's call per branch.
- After this lands, consider a permanent convention: **Codex always works in `/private/tmp/artemis-os-codex-<task>/` clean rooms** (not in `Desktop/Artemis/artemis-os-*` siblings). Sonnet Workers stay in `.claude/worktrees/` per the existing isolation pattern. The main `Desktop/Artemis/artemis-os` belongs to Lead + terminal-Lead only.
