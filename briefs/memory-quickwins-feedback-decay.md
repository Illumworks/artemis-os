# Brief: Memory quick-wins — retrieval feedback loop + maintenance scheduler

**For:** Codex (focused single-stream backend) — **model gpt-5.4, reasoning effort HIGH** (touches the
hot retrieval path; correctness/latency sensitive). **Back to:** app Opus Lead for live verify + merge.
Local-only git; branch `worker/memory-quickwins`; commit trailer
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work in your OWN worktree — never the main repo.

Context: `docs/memory-system-assessment-2026-06-04.md` (gaps #1 and #4). Both VERIFIED real:
`hit_count`/`accessed_at` are read in retrieval scoring but never written; `run_maintenance()` has no
scheduler/route/cron caller.

## Gap #1 — Retrieval feedback loop (the high-impact one)

`artemis/memory/retrieval.py` `search_observations()` fuses a score that WEIGHTS `hit_count`
(retrieval.py:131,167) and reads `accessed_at`, but no code ever increments `hit_count` or updates
`accessed_at`. So every observation is stuck at hit_count=0 forever and memory never learns what's
useful.

FIX: when `search_observations()` returns a result set, record usage for the observations actually
returned — `hit_count = hit_count + 1` and `accessed_at = now()` for those ids.
CRITICAL CONSTRAINTS:
- **Must NOT block or slow retrieval.** `search_observations` is on hot paths (Floating Artemis
  auto-injects memory every turn; agent context). Do the usage-write as a best-effort, fire-and-forget
  async task (mirror the best-effort pattern used for embedding writes) — a failure must never break
  or delay a search. A single batched UPDATE over the returned ids, not per-row.
- Increment for the observations included in the returned result set (the ones that won fusion and
  were surfaced). Simple +1 per appearance is fine — the decay job (#4) counterbalances runaway.
- Preserve the lossless invariant (this only touches score-feedback columns, never deletes/supersedes).

## Gap #4 — Maintenance/decay scheduler (polish, but real)

`artemis/memory/maintenance.py:44` `run_maintenance(session)` applies category-aware score decay but
nothing calls it → decay is inert → stale observations never age out of the active retrieval pool.

FIX:
- Register a **daily scheduled job** that calls `run_maintenance` in its own DB session. The app
  already runs APScheduler — see `artemis/pipelines/scheduler.py` (how the pipeline scheduler is
  created + started at app startup); add the memory-maintenance job alongside it (or the nearest
  correct seam). Confirm it actually registers on startup.
- Add a **manual trigger** `POST /api/memory/maintain` in `artemis/routes/memory.py` that runs
  `run_maintenance` once and returns its `{category: row_count}` result (the README/code comment
  already references this endpoint).
- Idempotent + safe to run repeatedly.

## Verify (live — the EFFECT)

- #1: hit a memory search (the memory search API or via Floating Artemis), then confirm the returned
  observations' `hit_count` incremented and `accessed_at` updated in the DB; and confirm a search
  returns just as fast (the usage-write is async/non-blocking — show it doesn't add latency).
- #4: `POST /api/memory/maintain` returns category→row counts and observation `score`s decayed per the
  multipliers; confirm the daily job is registered in the scheduler at startup.
- Unit tests for both. ruff + ruff format + mypy clean on touched files. No dependency add/upgrade.

## Handoff

Do NOT merge to main. Report files changed + live verification (before/after hit_count on a searched
observation; the maintain endpoint output; the registered job). App Opus Lead verifies live + merges.
Log in `../claudeck-artemis/COORDINATION.md`.
