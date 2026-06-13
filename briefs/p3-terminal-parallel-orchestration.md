# Terminal Orchestration — P3 parallel slices (gcal-mirror hardening + dismiss action-items)

**Owner:** terminal (orchestrator). **Codex is rate-limited**, so terminal runs these two ready, *independent*
P3 slices by spawning sub-agents. **Lead:** Artemis (Opus) merges both branches (handles the dismiss migration
on prod). Do-NOT-merge yourself — build + verify + report branches.

## Run shape: PARALLEL (safe) — or sequential if you prefer
The two slices touch **zero common files**, so fan them out as **two parallel sub-agents**, each in its **own
isolated git worktree + branch**. Sequential is also fine; parallel is faster and conflict-free here.

| Sub-agent | Spec brief (follow exactly) | Branch | Touches |
|---|---|---|---|
| A — gcal-mirror hardening | `briefs/p3-harden-gcal-mirror.md` | `worker/p3-harden-gcal-mirror` | `artemis/google_integration.py` + tests |
| B — dismiss action-items | `briefs/p3-dismiss-action-items.md` | `worker/p3-dismiss-action-items` | meetings/commitments + a migration + small FE |

## NON-NEGOTIABLE guardrails (AGENTS.md rule 6 + hard-won lessons)
1. **Each sub-agent in its OWN worktree**, never the main checkout. Commit on the branch **before reporting**.
2. **Each sub-agent uses its OWN test DB — do NOT share `artemis_test`.** Parallel agents that both TRUNCATE
   one `artemis_test` deadlock and wipe each other's seed. Use distinct names that still contain the substring
   `artemis_test` (conftest guard requires it): e.g. **`artemis_test_harden`** (A) and **`artemis_test_dismiss`**
   (B). Create + migrate each from its own worktree:
   `ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test_<x> uv run alembic upgrade head`
   then run pytest with **both** `ARTEMIS_DB_URL` and `ARTEMIS_TEST_DB_URL` set to that same DB.
3. Do **not** edit main's working tree. Do **not** merge — report the two branch names + each sub-agent's test
   result to Lead; Lead merges (applies B's migration to prod + restarts the launchd app service).
4. Drop the throwaway test DBs when done.

## Per-sub-agent acceptance (terminal verifies before reporting)
- **A:** the new regression tests pass — double-consent stays `active`, a revoked row heals on re-consent,
  connect-without-calendar does NOT revoke, disconnect still revokes.
- **B:** dismiss an action-item → leaves the open list + closes its linked commitment; re-summarizing the meeting
  does NOT resurrect it; raw item preserved (lossless); done/snooze still distinct.

## Report back to Lead
For each: branch name, files touched, test command + result (on its isolated DB), and any "pre-existing
failure" claims (Lead re-verifies those on the parent branch — worktrees lack `.env`, so env-coupled tests can
mislead). Then stop; Lead takes the merge.
