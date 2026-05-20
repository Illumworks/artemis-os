# Cleanup — Ruff Format Baseline

**Owner:** Codex (paste-ready, mechanical-only)
**Branch:** `codex/cleanup-ruff-format`
**LOC budget:** ~250 (full-diff insertions; cap at 300)
**STOP CONDITION:** if you reach 250 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** `cleanup-ruff-baseline` merged (which got `ruff check` to 0). This brief finishes the job for `ruff format`.

## Why this brief exists

After the ruff baseline cleanup, `./scripts/check.sh` still exits 1 — but now ONLY because `ruff format --check` fails on ~30 pre-existing files (~234 LOC of pure whitespace/formatting changes). This is the smallest possible remaining gap. Closing it makes `check.sh` a green CI gate end-to-end.

This is a **pure whitespace pass** — no behavior changes, no semantic edits, no judgment calls.

## Scope

### In scope

1. **Run `uv run ruff format`** on the affected files. Auto-applies the formatter.
2. **Confirm `uv run ruff format --check` returns 0.**
3. **Confirm `uv run ruff check` still returns 0** (no regression on the lint baseline).
4. **Verify no behavior changes** by running the full test suite — `uv run pytest` — and confirming no new failures.
5. **Sanity spot-check** one or two of the larger diffs: open the file, scroll through, confirm the changes are only whitespace/line-breaks (no logic edits, no imports added/removed, no string changes).

### Out of scope

- Any file outside what `ruff format --check` flags.
- Hand-editing any of the formatted files. The formatter is the source of truth.
- Adding new files. Format-only.

## Invariants

1. **No behavior changes.** Test suite must pass before and after with the same results. If a test newly fails, REVERT and ping Lead — that's a real signal the formatter touched something it shouldn't have (rare, but possible with weird docstring or string-literal edge cases).
2. **One commit.** Don't split into per-file commits — this is one logical pass.
3. **`# fmt: off` / `# fmt: on` blocks preserved.** Ruff respects these; verify the spot-check files still have them intact if any existed.

## Files expected

- ~30 files touched. Each diff is whitespace/line-break only.
- Total LOC change: ~234 per Codex's earlier measurement. Should fit under the 250 budget.

## Test plan

1. `uv run ruff format --check` returns 0.
2. `uv run ruff check` returns 0 (regression check).
3. `uv run pytest` test count matches pre-commit baseline (no new failures).
4. `./scripts/check.sh` now exits 0 end-to-end.

## What "done" looks like

1. `check.sh` is finally a green CI gate.
2. No behavior changes.
3. One commit on the cleanup branch.

## Report Codex submits

1. `git diff --stat` output (should be ~30 files, ~234 lines).
2. `./scripts/check.sh` exit status before/after (was 1, should be 0).
3. `uv run pytest` summary (assert same pass count as baseline).
4. Branch.

**INVARIANT: when done, run `git switch lead/j6a-granola-integration` so the main worktree doesn't sit on your branch.**

---

**Lead notes (not for Codex):**
- This is the smallest possible brief. After it lands, `check.sh` is healthy. Workers can rely on it as a real CI gate again. Every Worker since M1 has been operating with degraded signal; that closes today.
