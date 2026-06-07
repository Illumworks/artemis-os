# Brief — Pipeline Gate-1 approval must promote signals → campaign candidate

**Type:** P0 — the last gap in the auto marketing pipeline (scout→qualify→Gate-1 works; Gate-1→campaign
is broken). **Model:** Codex or terminal Sonnet. **Own worktree**, branch `worker/gate1-promote-candidate`,
cwd INSIDE; branch off `main`. **Own test DB**.

## Diagnosis (confirmed live 2026-06-06)

A clean `marketing.main` run (6b28ca77) reached `gate_1_signals_inbox` suspended with **5 qualified
signals** + sent the Slack card. Approving the gate via `POST /api/pipeline-runs/{run_id}/resume`
(node_id=gate_1_signals_inbox, decision=approved) resumed the run — but the next node
`content_brief_assembler` FAILED:
> "campaign initiation proposal requires exactly one uninitiated candidate for pipeline run <id>; found 0"

Root cause: the **pipeline Gate-1 (signal_brief) approval path resumes the run but never promotes the
run's qualified signals into a campaign_candidate.** The signal→candidate promotion
(`cluster_or_create_candidate` in `artemis/marketing/repository.py`) currently only runs in the MANUAL
per-signal path: `POST /api/signal-queue/{id}/approve` (`artemis/marketing/routes/signal_queue.py:~288`).
So the two Gate-1 approval paths have diverged — exactly the class of bug Group A fixed for Gate-2/content
(PIPE-1): all gate-decision paths must run the same side effects.

## Fix

When `gate_1_signals_inbox` is approved (via the pipeline resume/decision path — `pipelines/routes.py`
resume + the human_gate decision processing, see Group A's unified `_decide_*` pattern), it must promote
this run's qualified signals into a campaign candidate **linked to the pipeline_run_id**, so
`content_brief_assembler` finds exactly one uninitiated candidate. Reuse `cluster_or_create_candidate`
(or the same logic the manual `/approve` endpoint uses) over the run's qualified signals
(`signal_queue WHERE pipeline_run_id=<run> AND signal_status='qualified'`). Set the candidate's
`pipeline_run_id` so the scoping the assembler relies on matches. Decide multi-signal clustering: the 5
qualified signals should cluster into candidate(s) the way the manual path would (by district/family).
Mark the signals `approved` as the manual path does. Funnel BOTH paths (manual approve + pipeline gate)
through one shared promotion function so they can't drift again.

Confirm what `content_brief_assembler` expects ("exactly one uninitiated candidate for pipeline run") and
make the promotion satisfy it (one candidate per run, or adjust the assembler if multiple are legitimate —
coordinate, don't guess).

## Verify LIVE (assert the effect)
- Seed/replicate: a pipeline run suspended at gate_1 with N qualified signals (linked by pipeline_run_id).
  Approve via the resume/decision path → a campaign_candidate is created, linked to the run, uninitiated,
  in_inbox, with the signals attached + marked approved → `content_brief_assembler` succeeds (run proceeds,
  does NOT fail "found 0"). The manual `/approve` path still works unchanged.
- Existing pipeline + signal_queue tests pass.

## Constraints
- Lossless; status transitions only; no destructive migration. Reuse the existing promotion logic — don't
  fork it. Org dep rule nothing <7 days old. ruff + mypy + tests clean. Do NOT merge — report branch + how
  each effect was verified (esp. a real gate-resume → candidate created → assembler succeeds).
  Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
