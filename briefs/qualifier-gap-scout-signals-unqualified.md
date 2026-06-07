# Brief — Scout-emitted signals are never qualified (the campaign-never-proposed bug)

**Type:** P0 dormant-path bug. The whole scout→signal→qualify→Gate-1→campaign chain is dead at the
qualify step. **Model:** Codex or terminal Sonnet. **Own worktree**, branch
`worker/qualifier-scout-signals`, cwd INSIDE the worktree, branch off `main`. **Own test DB** (another
agent may run concurrently): `createdb artemis_test_qual; CREATE EXTENSION vector; ARTEMIS_DB_URL=...qual
uv run alembic upgrade head; export ARTEMIS_TEST_DB_URL=...qual`.

## Root cause (diagnosed live 2026-06-05 against artemis_os)

A fresh `marketing.main` run completed: all scouts succeeded, but `gate_1_signals_inbox` **skipped**
("upstream produced no signals") → **no campaign proposed**. The 14 fresh scout signals were left at
`signal_status='pending_qualification'` with NO `qualification_json` (never scored).

Why: deterministic qualification (`qualify_signal()` → fitScore → qualified/rejected) is invoked ONLY by
the HTTP routes via the route-private helper `_run_and_store_qualification` (`routes/signal_queue.py:502`,
called at intake L198 + qualify-endpoint L282). But scouts DON'T go through those routes — they persist
signals via:
- the agent tool `signal_queue.write` (`artemis/tools/signal_queue.py:172`) — creates row
  `signal_status="pending_qualification"`, resolves district, returns. **No qualification call.**
- `artemis/marketing/scout_runner.py:281` — same: creates `pending_qualification`, **no qualification.**

So every scout-emitted signal stays unqualified forever → never reaches Gate-1 → no campaign. (The
pipeline's `qualifier_*` agent nodes are LLM `agent_invocation`s that compose briefs; they do NOT run the
deterministic scorer either.)

## Fix

1. **Extract a shared qualification service.** Move the body of `_run_and_store_qualification` out of
   `routes/signal_queue.py` into a reusable function (e.g. `artemis/marketing/qualification.py` →
   `async def run_and_store_qualification(session, signal) -> dict|None`). Keep the route helper as a thin
   wrapper calling it (no behavior change to the routes). It already: loads active rulesets (returns None
   if none), builds territory configs, runs `qualify_signal`, annotates district tier, calls
   `save_signal_qualification`.
2. **Call it from both scout persist paths**, best-effort + non-fatal (mirror intake's "signal creation
   wins" semantics — wrap in try/except, log on failure, never block the write):
   - `artemis/tools/signal_queue.py` after `flush()` + district resolve (before returning).
   - `artemis/marketing/scout_runner.py` after each `SignalQueue` add/flush.
3. **Backfill the existing backlog** (149 `pending_qualification` signals incl. this run's 14): provide a
   one-shot path to qualify them — either reuse the existing batch qualify route if one exists, or a small
   idempotent script/endpoint that runs `run_and_store_qualification` over all `pending_qualification`
   rows. Lossless: only updates `qualification_json` + `signal_status`; never deletes.

## Verify LIVE (assert the EFFECT)

- Unit/integration: emit a signal via the `signal_queue.write` tool (and via scout_runner) → read the row
  back → `qualification_json` populated with a `fitScore` and `signal_status` is `qualified` or a
  `rejected_*`/`suppressed_*` state — NOT left `pending_qualification` (when an active ruleset exists).
- With NO active ruleset: signal still created, qualification skipped gracefully (non-fatal), no crash.
- End-to-end (live or seeded): a `marketing.main`-style flow where scouts emit qualifying signals →
  Gate-1 is NOT skipped → a campaign candidate is proposed. (Reconcile signal counts vs DB.)
- Confirm the existing route behavior (intake/qualify endpoints) is unchanged after the extraction.

## Constraints
- No schema/migration (qualification_json + signal_status columns already exist). Lossless — never delete
  signals; supersede/transition status only. Org dep rule: nothing <7 days old; commit uv.lock if
  regenerated. ruff + mypy + focused tests clean. Do NOT merge — report branch + how each effect was
  verified. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
