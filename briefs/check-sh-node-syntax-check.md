# Cleanup — Add `node --check` to check.sh

**Owner:** Codex (paste-ready, ~5-line shell change + sanity test)
**Branch:** `codex/check-sh-node-syntax-check`
**LOC budget:** ~40 (honest overrun OK to ~60)
**Brief author:** Lead (Opus 4.7)
**Depends on:** nothing.

## Why this brief exists

This session has shipped **three browser-crashing JS SyntaxErrors** in code that passed all backend + unit tests:

1. `formatRelativeTime` duplicate declaration in operations-shell.js (PIPE2 merge)
2. `val` ReferenceError + missing tz listener in trigger-scheduled-form.js (cron presets — caught at merge by terminal)
3. `cron-utils.js:98` Unexpected token (cron presets — surfaced only in browser)

Each one made the entire SPA fail to mount (left rail HTML renders but no click handlers bind). Each one was a 5-minute fix once located. Each one would have been caught instantly by `node --check`.

The structural fix: **add `node --check` to `./scripts/check.sh`** so syntax errors fail the CI gate before merge.

## Scope

### In scope

1. **Add a step to `./scripts/check.sh`** that runs `node --check` on every `.js` file under `public/js/`:

```bash
echo "→ JS syntax check (node --check)"
JS_FAILED=0
while IFS= read -r -d '' jsfile; do
  if ! node --check "$jsfile" 2>&1; then
    JS_FAILED=1
  fi
done < <(find public/js -type f -name "*.js" -print0)
if [ "$JS_FAILED" -eq 1 ]; then
  echo "✗ JS syntax check failed"
  exit 1
fi
echo "✓ All JS files parse"
```

Place this step early in check.sh — BEFORE ruff/mypy/pytest so it fails fast.

2. **Verify** by intentionally introducing a syntax error in a test file (e.g., `public/js/components/test-syntax-canary.js` with `let x = ;`), running `check.sh`, confirming it fails at the new step. Then delete the canary.

3. **Test:** add a tiny pytest that runs check.sh and asserts exit 0 within exempt set. (Optional — if `check.sh` is already covered by CI, skip.)

### Out of scope

- ESLint or Prettier integration. Just syntax validation; style is a separate concern.
- TypeScript checking. The project is vanilla JS; no TS.
- Browser-level validation (does the page actually render). That's runtime testing, separate concern.
- Pre-commit hook (.git/hooks/pre-commit) wrapping this. Optional add later if Workers keep skipping check.sh.

## Invariants

1. **node --check is fast** (millisecond per file). Adding it doesn't materially slow check.sh.
2. **The step fails loud** with the exact filename + line number of any syntax error.
3. **No new dependencies.** `node` is already a dev requirement.

## Files expected

| File | LOC |
|---|---|
| `scripts/check.sh` | ~15 delta (the new step) |
| `docs/CHECK-SCRIPT.md` if it exists | ~10 delta (document the new step) |
| Optional test for check.sh wrapper | ~15 |

**Total: ~30 LOC.** Cap 60.

## Test plan

1. Run check.sh on current lead → passes (no syntax errors expected post-merge)
2. Introduce a deliberate syntax error in any .js → check.sh fails at the new step with filename + line
3. Remove the syntax error → check.sh passes again
4. Confirm step ordering: syntax check runs BEFORE ruff/mypy/pytest (fail fast)

## Invariants Codex must NOT regress

- No new dependencies
- check.sh exit code conventions preserved (0 = pass, non-zero = fail)
- Existing check.sh steps unchanged
- `git switch lead/j6a-granola-integration` after commit

## What "done" looks like

1. check.sh has a JS syntax check step early in the pipeline
2. Running check.sh on a clean tree exits 0
3. Running check.sh on a tree with a deliberate JS syntax error exits non-zero with clear message

## Report Codex submits

1. `git diff --stat`
2. Paste the new step exactly as it lands in check.sh
3. Demo: introduce a fake syntax error → check.sh output (paste). Then remove → re-run → passes.
4. Branch

---

**Lead notes (not for Codex):**
- Three preventable bugs this session is the threshold for codifying the rule. The fix is so small (5-line shell) that NOT doing it would be the slop pattern.
- After this lands, every future Worker / Codex paste that ships .js will be caught at the gate. Future Sonnet reports of "browser smoke not run" become survivable because check.sh has the syntax safety net.
