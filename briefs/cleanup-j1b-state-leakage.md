# Cleanup — j1b Test State Leakage (close 2 exempt failures)

**Owner:** Codex (paste-ready, mechanical diagnostic + fix)
**Branch:** `codex/cleanup-j1b-state-leakage`
**LOC budget:** ~120 (honest overrun OK to ~160)
**Brief author:** Lead (Opus 4.7)
**Depends on:** nothing. Independent of PIPE3.

## Why this brief exists

Two tests have been in the documented exempt set for multiple sessions:
- `tests/test_j1b_credential_entry.py::test_get_config_before_any_save`
- `tests/test_j1b_credential_entry.py::test_delete_clears_config`

Both **pass when run alone but fail in the full suite.** Classic test-isolation symptom: some other test in the suite leaves state behind that breaks j1b's "before any save" assertion. We've banked it long enough; close it now.

## Symptom

Tests assert `body["ever_configured"] is False` but get `True` — meaning a credentials row exists in the DB at the start of the test, when the test expects an empty state.

## Hypothesis (in priority order)

1. **conftest fixture doesn't TRUNCATE `integration_credentials` table** (or whatever table backs the j1b credential storage). Earlier tests in the run populate it; j1b inherits the leftover state.
2. **A test elsewhere in the suite writes credentials but doesn't clean up.** Same effect.
3. **Module-level mock/state in `artemis/routes/integrations.py`** (or wherever j1b lives) doesn't reset between tests. Less likely with conftest TRUNCATE, but possible.

## Scope

### In scope — diagnose and fix

1. **Find the credentials table.** Grep for `integration_credentials` / `credentials` / `j1b` in:
   - `artemis/integrations/models.py` (or wherever models live)
   - `alembic/versions/` (look for the migration that created it)
   - `tests/conftest.py` and any sub-conftests

2. **Check the conftest TRUNCATE list.** The main conftest probably has a list of tables to truncate per-test. If `integration_credentials` (or equivalent) isn't in that list, add it.

3. **Run the failing tests in isolation, confirm they pass.** Establish baseline.

4. **Run the full suite, confirm failure.** This is the bug we're fixing.

5. **Add `integration_credentials` (and any related tables — secrets, oauth_tokens, etc.) to the conftest TRUNCATE.** Verify isolation works.

6. **Run full suite again, confirm j1b tests now pass.** Other tests should not regress.

7. **If TRUNCATE alone doesn't fix it:** look for module-level state. The j1b route may cache "ever_configured" status in a module variable. If so, reset it via a fixture.

### Out of scope

- Refactoring the j1b route's logic. Just fix isolation.
- Removing the j1b tests. Diagnose the real cause; don't sweep under the rug.
- Investigating other exempt failures (Jira no-project + memory_drill flake). Different scope.
- Adding new test infrastructure (parallel test runners, etc.). Use what's there.

## Invariants

1. **Both j1b tests pass in isolation AND in the full suite** after this lands.
2. **No other test regresses.** The fix must not break other tests that rely on whatever state was being leaked.
3. **conftest TRUNCATE remains hard-fail on non-test DB** (commit `f083ab4`). Don't soften that invariant.
4. **No silent test skipping.** If a test can't be made to pass cleanly, surface it; don't `pytest.skip()` it.

## Files expected

| File | LOC |
|---|---|
| `tests/conftest.py` (or sub-conftest) | ~5 delta (add table(s) to TRUNCATE list) |
| Possibly `artemis/routes/integrations.py` if module-level state needs reset | ~10 delta |
| `tests/test_j1b_credential_entry.py` | Verify, do NOT modify the assertions. If modification needed, flag for Lead |
| New fixture if module-level reset needed | ~15 |

**Total: ~30 LOC honest** — most diagnostic work is reading + running tests, not writing code. Cap 160 in case it surfaces a deeper issue (then STOP and ping Lead).

## Test plan

1. **j1b alone:** `uv run pytest tests/test_j1b_credential_entry.py -v` → both pass (baseline).
2. **j1b in suite:** `uv run pytest tests/` → both currently fail (baseline).
3. **After fix, j1b alone:** still both pass.
4. **After fix, full suite:** both pass; pre-existing exempt failures (Jira no-project + memory_drill flake) unchanged.
5. **No new failures.** Total passed count goes up by 2 from baseline.

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set (or, in this case, with the j1b failures dropping OUT of the exempt set — net effect: fewer exempt failures, cleaner gate)
- `git switch lead/j6a-granola-integration` after commit

## What "done" looks like

1. Both j1b tests pass in full suite.
2. Exempt set drops to 1 (just Jira no-project; memory_drill flake stays if intermittent).
3. Diagnosis written up in commit message (what was leaking, what fixed it).
4. No regressions elsewhere.

## Report Codex submits

1. `git diff --stat` output.
2. **Diagnosis** — paste the root cause clearly (was it conftest? was it module state? something else?).
3. `pytest` before/after numbers.
4. Whether any other test relied on the leaked state (regression check).
5. Branch.

---

**Lead notes (not for Codex):**
- This is a small surgical fix with high signal-to-noise. Closing 2 exempt failures means `check.sh` becomes cleaner; Workers' "X failed in exempt set" reports get shorter and clearer.
- If diagnosis reveals something more architectural (e.g., j1b uses sessions in a way that needs broader test refactoring), STOP and ping Lead. Don't expand scope.
