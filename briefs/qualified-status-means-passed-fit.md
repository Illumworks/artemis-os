# Brief — `signal_status='qualified'` must mean "passed the fit bar," not just "scored"

**Type:** P1 correctness (closes the scout→campaign loop). **Model:** Codex or terminal Sonnet.
**Own worktree**, branch `worker/qualified-means-passed`, cwd INSIDE the worktree, branch off `main`.
**Own test DB**: `createdb artemis_test_fit; CREATE EXTENSION vector; ARTEMIS_DB_URL=...fit uv run alembic
upgrade head; export ARTEMIS_TEST_DB_URL=...fit`. Do NOT touch artemis_os — Lead applies to live after merge.

## Problem (traced + confirmed 2026-06-05)

`run_and_store_qualification` (`artemis/marketing/qualification.py:~128`) transitions
`pending_qualification → qualified` **unconditionally** on any successful scoring — even when the signal
scored 0.0 and `passesMinFitScore=false`. So `signal_status='qualified'` currently means "has been scored,"
NOT "passed the fit bar." Every downstream selection uses bare status with NO fit check:
- Gate-1 inbox: `human_gate_executor.py:~336` — `WHERE signal_status == 'qualified'`
- Signals inbox list: `repository.py:list_signals` (status filter only)
- Campaign-candidate promotion: `signal_queue.py approve_signal` (`if signal_status != qualified`) →
  `cluster_or_create_candidate`
- Brief composer: reads attached candidate signals (no fit filter)

Net: a 0.0-score signal can be surfaced to humans and promoted to a campaign. Live today: 204/257 signals
pass min_fit, but all ~257 scored ones are `qualified`; ~53 zero/low-score signals are wrongly `qualified`.

## Fix (make the status honest at the source — one change)

In `run_and_store_qualification`, after scoring, **set `signal_status` from the fit result** instead of an
unconditional up-transition:
- If the signal passes min_fit in ANY family (i.e. `any(s.passesMinFitScore for s in scores)`, equivalently
  `max(adjustedScore) >= min_fit_score`) → `qualified`.
- Else → leave/return to `pending_qualification` (it's eligible to re-qualify if rulesets improve — this is
  the lossless, re-evaluatable state; do NOT invent a new state / no migration).
- This must also DEMOTE a currently-`qualified` signal that now fails fit (qualified → pending_qualification).
  Check `artemis/marketing/state_machine.py` allows that transition; if it doesn't, add it (qualified →
  pending_qualification is a legitimate re-evaluation, lossless — no data deleted). If the transition truly
  can't be made clean, STOP and report rather than forcing it.
- Keep the "no active rulesets → skip, leave pending, non-fatal" behavior intact.

Do NOT add fit filters to the 4 selection paths — fixing the status at the source makes them all correct.

## Correct existing data

Provide a way to re-evaluate all already-scored signals so the ~53 below-threshold ones demote
qualified→pending (reuse the existing `--rescore-all` path in `scripts/seed_josh_rulesets.py` or the
backfill — confirm a re-score now applies the fit-gated status). Lossless: status transitions only; never
delete, never touch qualification_json scores beyond re-computing them.

## Verify (on your test DB; assert the EFFECT)

- Seed an active ruleset + reason codes. A signal with a HOT code (≈0.9) → `signal_status='qualified'`.
- A signal whose best family score is 0.0 / below 0.5 → `signal_status='pending_qualification'` (NOT
  qualified), `qualification_json` still populated with the scores.
- A previously-`qualified` signal that now scores below threshold → demoted to `pending_qualification` on
  re-score.
- The Gate-1 query (`WHERE signal_status='qualified'`) and the signals-inbox list now return ONLY
  fit-passing signals — confirm a 0.0 signal does not appear.
- Update any existing test that assumed unconditional pending→qualified (e.g. in
  `test_qualifier_scout_gap.py`) to seed a fit-passing ruleset/signal so the assertion still holds for the
  right reason.

## Constraints
- Lossless; status transitions only; no schema/migration unless the state-machine demotion legitimately
  needs the transition added (that's a state_machine edit, not a DB migration). Org dep rule: nothing
  <7 days old. ruff + mypy + focused tests clean. Do NOT merge, do NOT touch artemis_os. Report branch +
  SHA + worktree path + how you verified each effect (esp. the demotion + the gate query now excluding
  0.0 signals). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
