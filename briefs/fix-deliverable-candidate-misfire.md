# Brief: Fix brief-less candidate → wrong-candidate draft + empty Gate-2

**For:** Codex — **model gpt-5.4, reasoning effort HIGH** (correctness + data-leak risk; needs
pipeline-handoff investigation). **Back to:** app Opus Lead for live verify + merge.
Local-only git; branch `worker/fix-deliverable-candidate-misfire`. Commit trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## The bug (reproduced live)

A campaign_deliverables run for a candidate that LACKS a campaign brief produces a broken,
unsafe result. Run `b5ccc85d-71fa-486c-b976-c3728715b19e` had target_candidate_id=3 (candidate 3
= the original Slack-smoke candidate, which has no campaign brief). Nodes reported "succeeded" but:

1. **Wrong-candidate draft (the dangerous one):** `content_writing_studio_adapter` created
   `campaign_deliverables` row **id=8 tagged to candidate_id=5** — NOT the run's target candidate 3.
   The agent grabbed stray runtime handoff context for candidate 5. **A draft for one campaign got
   written into another campaign.** This is a correctness/data-leak risk.
2. **Failure ignored:** a second node (`deliverable_outreach_email`, same writing_studio_adapter
   agent) FAILED — output_summary: *"I'm missing critical handoff information ... Missing:
   campaign_brief_id — not found in runtime"* — yet the run still advanced.
3. **Empty human gate:** Gate-2 (approval id=32) fired anyway with `deliverable_ids=[]` and empty
   `draft_body`, producing a "Draft body not yet available" Slack card with nothing to review.

## Required fixes (investigate first, then fix the root)

- **Deliverable must bind to the run's target candidate.** Find where the draft's candidate is
  resolved (artemis/marketing/writing_studio/invoke.py `create_draft_from_candidate`,
  artemis/tools/content_agent_tools.py `writing_studio.enqueue`, and how the agent receives the
  candidate id in shared_context — artemis/pipelines/node_executors/agent_executor.py). Make the
  candidate id AUTHORITATIVE from the pipeline run's `target_candidate_id` — never inferred from
  stray/most-recent runtime context. A run targeting candidate 3 must never write a deliverable for
  candidate 5.
- **A brief-less / failed run must not reach a human gate with nothing.** Either fail the run fast
  when the campaign brief is missing, or make the Gate-2 human gate refuse to fire (and instead
  fail/flag the run) when there is no deliverable for THIS candidate. A human gate presenting an
  empty draft is itself a bug.
- **Investigate why candidate 3 has no brief** and whether the initiation flow should guarantee a
  brief exists before a deliverables run is dispatched (artemis/marketing/routes/initiation.py).
  Recommend the right seam; fix it if it's clearly the initiation path.

## Verify (live — the EFFECT, not unit-green)

- Trigger a campaign_deliverables run for a brief-LESS candidate → it must EITHER fail cleanly
  (no human gate, clear error) OR produce a deliverable correctly tagged to that candidate.
- Trigger one for a candidate WITH a brief (e.g. candidate 5 has a working path) → still produces a
  correctly-tagged, non-empty draft that reaches Gate-2. (Regression guard.)
- Confirm no deliverable is ever created with a candidate_id != the run's target_candidate_id.
- Unit tests for the candidate-binding + the no-empty-gate guard.

## Guardrails

- Local-only git; own branch. ruff + ruff format + mypy clean on touched files.
- Do NOT touch OKR rows or Writing Studio *rules*; no dependency add/upgrade.
- Clean up the stray artifacts if safe: deliverable id=8 (mis-tagged to candidate 5) and the junk
  awaiting_approval run b5ccc85d / approval 32 are test residue — note them; the Lead can purge.
- If the fix is larger than the candidate-binding + gate-guard (e.g. a deeper handoff-context
  redesign), STOP and report findings rather than expanding scope.

## Handoff

Report the root cause, files changed, the candidate-binding fix, the no-empty-gate guard, and your
live verification (run ids + that deliverables are correctly tagged). The app Opus Lead verifies
live + merges to main. Log progress in ../claudeck-artemis/COORDINATION.md.
