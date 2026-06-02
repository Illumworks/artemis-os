# Cleanup — Ruff Baseline + Worktree Exclusions

**Owner:** Codex (paste-ready, mechanical-only)
**Branch:** `codex/cleanup-ruff-baseline`
**LOC budget:** ~60 (full-diff insertions; cap at 80)
**STOP CONDITION:** if you reach 60 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** nothing. Cleanup of accumulated lint debt.

## Why this brief exists

`./scripts/check.sh` has been failing on pre-existing ruff errors for several sessions. Every Worker (M1, M5, M3, cleanup, O5) reports "unrelated to my changes" — which is true, but means the script no longer functions as a pass/fail gate. Workers can't tell their own regression from baseline noise. **The longer we let this drift, the more invisible real regressions become.**

Investigation reveals two distinct problems:

1. **Ruff is scanning stale worker worktree directories.** `.claude/worktrees/agent-*/` and `.clone/agent-*/` contain copies of the codebase from prior workers that were never cleaned up. These account for ~118 of the 137 total ruff errors. Excluding them is a one-line config change.

2. **~19 real errors in the main tree.** Most are auto-fixable (unused imports, import sorting). One is a `byType` mixedCase variable I introduced when stubbing `/api/stats/agent-metrics` to match Node's JSON shape (task #13). A few F821 (undefined name) need actual investigation — these may be real bugs hidden behind the noise.

After this cleanup, `./scripts/check.sh` either passes or fails on a small known set of issues we've explicitly chosen to defer. Workers can trust it again.

## Scope

### In scope

1. **Exclude stale worktree directories from ruff** — edit `pyproject.toml` (or wherever ruff config lives):
   ```toml
   [tool.ruff]
   extend-exclude = [
       ".claude/worktrees",
       ".clone",
       ".sync",
   ]
   ```
   These are local-only worker artifacts; they should never be linted. Adding `.sync` defensively in case any worker process uses it.

2. **Run `uv run ruff check --fix`** — auto-fix the 113 fixable errors. Most are F401 (unused imports) and I001 (import order). Auto-fix is safe for these classes.

3. **Manually address the remaining errors** after auto-fix runs. As of the brief's snapshot, the categories are:
   - **F401** unused imports — auto-fix handles all
   - **I001** import sorting — auto-fix handles all
   - **F821** undefined name (~10) — INVESTIGATE EACH. These may be real bugs. If a name is genuinely undefined, add the missing import. If it's a leftover from removed code, delete the offending reference. DO NOT silence with `# noqa` unless you genuinely understand why the name should be missing.
   - **N815** mixedCase in class scope (~6) — `byType` in `artemis/routes/stats.py:132` is intentional (matches Node's JSON shape). Add `# noqa: N815` with comment explaining the wire-shape compatibility. The other 5: investigate; rename to snake_case if it's not wire-shape constrained; `# noqa` with reason if it is.
   - **B008** function call in default arg (3) — usually `def foo(x=datetime.now())`. Refactor to `def foo(x=None): x = x or datetime.now()`. If unfixable (FastAPI Depends patterns are fine), `# noqa: B008` with reason.
   - **B018** useless expression (2) — usually a test statement like `obj.attribute` that should be `assert obj.attribute`. Fix.
   - **B904** raise without from (1) — `raise X` inside `except Y as e:` should be `raise X from e`. Fix.

4. **Verify final state**: `uv run ruff check` returns 0 errors. If any remain, they must be `# noqa`-suppressed with a one-line comment explaining why.

5. **`./scripts/check.sh` passes** end-to-end (or fails only on a documented small set with clear reasons).

### Out of scope

- Removing the stale worktree directories themselves. That's terminal-Lead's job post-merge (`git worktree remove`). This brief just excludes them from linting.
- mypy / pyright errors. Different cleanup, different brief.
- Test fixture cleanups beyond the lint errors. Out of scope.
- Renaming files or moving modules. Out of scope.

## Investigation guidance for F821 (undefined name)

These are the highest-risk fixes. Don't auto-anything. For each:

1. Read the file context (5 lines before + 5 after the F821 line).
2. Decide:
   - **Missing import?** Add it.
   - **Typo?** Fix the typo.
   - **Removed code leftover?** Delete the dead reference.
   - **Conditional import that ruff can't see?** `# noqa: F821` with one-line comment explaining the conditional.
3. If you can't tell which case applies, STOP and flag for Lead. Don't guess on F821 — it's the rule most likely to be hiding a real bug.

## Invariants

1. **No behavior changes.** This is purely lint cleanup. If a fix changes runtime semantics (e.g., removing what looked like an unused import but was actually triggering a side effect), revert and ask Lead.
2. **No `# noqa: <rule>` without a reason comment.** Every silence must explain itself in code.
3. **Auto-fix runs in one shot** — `ruff check --fix` once, then review the diff. Don't iterate auto-fixes; that obscures intent.

## Files expected

- `pyproject.toml` — `extend-exclude` addition. ~5 LOC.
- Auto-fixed files: ~10-15 files touched by `ruff --fix`. Each diff is small (import removals + sort).
- Manually-fixed files: ~5-8 files for F821 / N815 / B008 / B018 / B904. ~20 LOC delta.

Total: ~50 LOC. Well under the 60 cap.

## Test plan

1. **`uv run ruff check` returns 0** (or only `# noqa`-explained exemptions).
2. **`./scripts/check.sh` runs cleaner** — at minimum, no longer fails on ruff. Other check.sh steps (mypy, pytest) must continue to pass at their pre-existing baselines.
3. **Spot-test the F821 fixes:** if any F821 fix imported a missing module, run a test that imports the affected file to confirm no `ImportError` at runtime.
4. **Spot-test the N815 in stats.py:** `GET /api/stats/agent-metrics` still returns JSON with `byType` (camelCase) in the response. Wire shape preserved.

## What "done" looks like

1. `pyproject.toml` excludes `.claude/worktrees`, `.clone`, `.sync`.
2. `uv run ruff check` returns 0 errors.
3. `./scripts/check.sh` passes ruff stage cleanly.
4. F821 errors all resolved (fixed or explicitly suppressed with reason).
5. `byType` in stats.py preserved with `# noqa: N815` and wire-shape comment.

## Report Codex submits

1. `git diff --stat` output.
2. Pre/post ruff error count.
3. List of F821 fixes with one-line reasoning each (paste — Lead reviews).
4. Any `# noqa` added (paste — should be ≤ 5 total).
5. `./scripts/check.sh` exit status before/after.
6. Branch.

---

**Lead notes (not for Codex):**
- After this lands, `./scripts/check.sh` is once again a meaningful CI gate. Workers can rely on it to differentiate their regressions from baseline noise.
- The worktree exclusion is the biggest single win. Even without fixing a single error, that one-line change drops 118 of 137 errors. The remaining 19 are then fast.
- F821 is the only investigation risk. If Codex flags any of them as ambiguous, treat each as a potential bug surface and review carefully before silencing.
