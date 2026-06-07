# Brief — Test-suite isolation (shared test DB contention)

**Type:** bounded chore (NOT a feature). **Fire AFTER the solidity tail is merged.**
**Model:** Codex or terminal Sonnet. **Own worktree**, branch `worker/test-isolation-shared-db`,
launched with `cwd` INSIDE the worktree (do not touch the main repo working tree).

## Problem (precisely scoped — this is NOT app instability)

`./scripts/check.sh` runs the full pytest suite and drifts into failures that are **test-harness
contention, not app bugs**. Proof: every flagged test PASSES in isolation —
`tests/test_no_direct_status_writes.py` (19), `artemis/builders/tests/test_agents.py`,
`tests/test_memory_drill.py` (6) all green when run alone; they only fail in the full run.

Root cause = **non-isolated shared test database** (`artemis_test`). Many conftests do
`TRUNCATE … RESTART IDENTITY CASCADE` on shared tables before each test. When tests run in
parallel / interleaved, they:
- deadlock on concurrent `TRUNCATE … CASCADE` (observed: alembic 0023 up/down roundtrip,
  gate-card tests),
- wipe seed data a sibling test depends on (observed: `test_memory_drill` "live=0 drill=1";
  `test_backup_prune_keeps_latest` skipped because sentinel tables emptied by another test).

**Do NOT "fix" these by changing app code.** The app is correct. Fix the test harness.

## Goal

`./scripts/check.sh` is a trustworthy green/red signal again — the full suite passes deterministically,
in one command, without per-file cherry-picking.

## Approach (pick the lightest that works; verify before expanding scope)

1. **Serialize the DB-mutating tests.** Confirm/lock the suite to no parallelism for DB tests
   (`-p no:randomly` already helps; ensure no `pytest-xdist -n` is fanning out TRUNCATEs). Establish
   a single, ordered execution for anything that TRUNCATEs shared tables.
2. **Isolate per-test data** instead of cross-test TRUNCATE where feasible: transaction-rollback
   fixtures, or per-test schema/namespacing, so one test can't wipe another's rows.
3. **Fix the seed-dependent drill tests** (`test_memory_drill`) so they seed their own fixtures
   rather than assuming pre-existing rows survive sibling truncation.
4. **The alembic up/down roundtrip deadlock** (`test_j9b_slack_triage_polish::test_migration_0023_up_down_roundtrip`):
   make it run against an isolated connection/schema so the downgrade doesn't lock against other tests.

## Hard constraints

- **Zero app-code behavior change.** Touch only `conftest.py` files, fixtures, `scripts/check.sh`,
  pytest config, and the affected test files' setup/seeding. If you believe a test exposes a *real*
  app bug, STOP and report it — do not silently change app code under cover of "test fix."
- **Lossless / data-safety:** never point a TRUNCATE at `artemis_os` (the live DB). The existing
  `REFUSING TO LOAD … is not the test database` guard must stay intact (it caught exactly this).
- Dependency rule: no dep added/upgraded < 7 days old (org policy). Commit `uv.lock` if regenerated.

## Done = 

- `ARTEMIS_TEST_DB_URL=…/artemis_test ./scripts/check.sh` passes **fully and repeatably** (run it
  3× to prove determinism), JS + ruff + ruff-format + mypy + full pytest all green.
- Report: what was contention vs. what (if anything) was a real bug you escalated rather than masked.
- Do NOT merge — report branch + the 3× green check.sh output for Lead to verify + merge.
- Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
