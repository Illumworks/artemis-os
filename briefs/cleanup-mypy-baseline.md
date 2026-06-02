# Cleanup — Mypy Baseline

**Owner:** Codex (paste-ready, mechanical-only)
**Branch:** `codex/cleanup-mypy-baseline`
**LOC budget:** ~80 (full-diff insertions; cap at 100)
**STOP CONDITION:** if you reach 80 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** ruff baseline + format both merged. This brief finishes the CI gate.

## Why this brief exists

After ruff is green (cleanup-ruff-baseline + cleanup-ruff-format), `./scripts/check.sh` still exits 1 — the next gate is **mypy with 7 pre-existing type errors** in `artemis/providers/tests/test_codex_adapter.py` and `artemis/routes/stats.py`. This brief closes the last gap. After it lands, `check.sh` is green end-to-end and Workers can trust it as a real CI gate again.

## Scope

### In scope

1. **Snapshot the 7 errors.** Run `uv run mypy artemis/providers/tests/test_codex_adapter.py artemis/routes/stats.py` (or however `check.sh` invokes mypy) and capture the 7 errors verbatim.

2. **For each error, choose ONE of three responses:**
   - **Real type fix.** Add the missing annotation, fix the wrong type, narrow the union. Default choice when the type is genuinely wrong.
   - **`# type: ignore[<error-code>]` with reason comment.** When the type system can't express the actual constraint (common in test fixtures, mocks, dynamic dispatch). One-line comment explains why ignored.
   - **Refactor the offending line.** When the type error reveals a real ambiguity (e.g., `Optional` not handled). Refactor to make the type system happy AND the code clearer.

3. **No silencing without reasoning.** Every `# type: ignore` gets a one-line "why" comment. Lead reviews the list.

4. **Verify `uv run mypy` returns 0** (or only the explicitly-ignored-with-reason set, which is also fine).

5. **Verify `./scripts/check.sh` exits 0** end-to-end.

### Out of scope

- mypy errors in any other file. If the listed files reference other modules whose types are wrong, fix only the surface error in the target file; don't chase upstream.
- New strict-mode flags. Don't increase mypy strictness in this brief.
- Type stubs for third-party packages. Out of scope.

## Investigation guidance for each error

`test_codex_adapter.py` errors are likely mock/fixture-related (untyped fixtures, dict literals returned where structured types expected). Common patterns:
- `Argument N to "X" has incompatible type "dict[str, Any]"; expected "Y"` → cast() or `# type: ignore[arg-type]` with reason.
- `"None" has no attribute "Z"` → add an assertion or `Optional` narrow.

`routes/stats.py` errors likely relate to the `byType: list[dict]` shape (which already has a `# noqa: N815` for the camelCase wire match). Common patterns:
- Untyped `list[dict]` → `list[dict[str, Any]]` is the minimal fix.
- Pydantic model field default factory mismatches → cast the default.

For each, pick the cheapest fix that doesn't change runtime behavior.

## Invariants

1. **No runtime behavior change.** This is type-system cleanup. Tests must pass before and after with same results.
2. **No `# type: ignore` without a reason comment.** Every silence explains itself.
3. **No new mypy strictness flags.** Match the existing baseline.

## Files expected

- `artemis/providers/tests/test_codex_adapter.py` — ~5-10 LOC delta.
- `artemis/routes/stats.py` — ~5-10 LOC delta.
- Possibly `mypy.ini` / `pyproject.toml` if any of the 7 errors needs a per-module override (rare; prefer in-file `# type: ignore`).

Total: ~20 LOC. Well under cap.

## Test plan

1. `uv run mypy` returns 0 (or only explained ignores).
2. `uv run pytest` test count matches pre-commit baseline (no behavior change).
3. `uv run ruff check` and `uv run ruff format --check` both return 0 (no regression on prior cleanups).
4. `./scripts/check.sh` exits 0 end-to-end. **This is the final acceptance criterion.**

## What "done" looks like

1. mypy returns 0.
2. `check.sh` exits 0 — green CI gate end-to-end for the first time in many sessions.
3. Every `# type: ignore` added has a one-line reason comment.

## Report Codex submits

1. `git diff --stat` output.
2. The 7 original errors (paste, verbatim from initial mypy run).
3. For each: which fix was chosen (real fix / ignore-with-reason / refactor) and one-line reasoning.
4. `./scripts/check.sh` exit status before/after (should be 1 → 0).
5. Branch.

**INVARIANT: when done, run `git switch lead/j6a-granola-integration` so the worktree doesn't sit on your branch.**

---

**Lead notes (not for Codex):**
- This is the last cleanup brief in this wave. After it lands: ruff check, ruff format, mypy, pytest all pass via `check.sh`. Workers can trust the gate. Every regression report becomes meaningful.
- If any of the 7 errors reveals a real bug (rare but possible — types do catch things), flag for Lead. Don't silently ignore.
