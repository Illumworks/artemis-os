# Worker Brief — Initiate Campaign #18 End-to-End (HB27, Friday demo)

**Owner:** Codex (backend execution). **Lead:** Artemis (Opus) verifies + reports.
**Status:** READY (demo-critical, Friday). **No external dependency.**
**Context:** Lead already deleted the 3 mock campaigns and promoted hot signal #624
("Texas Personal Financial Literacy Course Requirement (HB27)") to **campaign_candidate id=18**
(stage `human_gate_1`, `in_inbox`, family `general_growth`). Jon wants #18 built end-to-end so it
demos as a complete real campaign.

## Goal
Take candidate **18** from Gate-1 inbox to a fully-initiated campaign with **generated deliverables**,
using the REAL initiation flow, then verify the artifacts exist.

## Why a worker (not a standalone one-liner)
The running app enforces CF Access, so the HTTP API is not callable from localhost without a JWT. And the
deliverables run executes via the app's pipeline executor (`_dispatch_execution` from
`artemis.pipelines.routes`; the pipeline scheduler is cron-only and will NOT auto-pick a queued run). So
drive the **route handler functions in-process** (they bypass the HTTP auth `Depends`), and **run the
dispatched deliverables pipeline run to completion in the same process** (await the executor) so content is
actually generated before the script exits. Import all model modules first
(`artemis.pipelines.models`, `artemis.marketing.models`, `artemis.integrations.models`) so cross-table FKs
resolve (the `signal_queue.pipeline_run_id -> pipeline_runs` mapper error bites otherwise).

## Steps
1. **Generate the proposal:** call `get_initiation_proposal(candidate_id=18, session=...)`
   (`artemis/marketing/routes/initiation.py:127`) — it runs `_load_or_generate_proposal`, stores
   `initiation_proposal_json` on the candidate, and returns `proposal` (name, objective, target_scope),
   `deliverableRegistry` (active slugs + defaultEnabled), and `defaultTargetScope`.
2. **Assemble + persist the brief:** the initiate handler requires a brief
   (`get_campaign_brief(session, 18)` must be non-None — `repository.py:755`). Use
   `brief_assembler.assemble_brief(...)` (`brief_assembler.py:152`) + `create_campaign_brief(...)`
   (`repository.py:735`) to build and store it for #18.
3. **Initiate:** build `InitiateCampaignRequest` (`initiation.py:74`) from the proposal — `name`,
   `objective`, `target_scope` (use the proposal's, or `defaultTargetScope` which is the signal-derived
   default; HB27 is a Texas signal so expect a TX-narrowed scope, do not force all-1903-districts),
   `deliverable_type_slugs` = the **default-enabled active** slugs from the registry (≥1). Call the
   `initiate(candidate_id=18, body=..., session=...)` handler. It runs `initiate_campaign`, creates the
   deliverables pipeline run, and dispatches it.
4. **Run the deliverables pipeline to completion:** since `_dispatch_execution` is app-bound, after
   `initiate` returns the `deliverableRunId`, **await the pipeline executor on that run id** directly
   (find the executor entrypoint in `artemis/pipelines/executor.py` / `pipelines/routes.py`) so the
   deliverables are actually generated in-process. Do not just leave the run `queued`.
5. **Verify:** `campaign_candidates` row 18 is `initiated` (initiated_at set, decision/stage advanced);
   a `campaign_briefs` row exists for 18; `campaign_deliverables` rows exist for 18 with generated content;
   the deliverables `pipeline_runs` row is `succeeded`. Report the counts + a short sample of the generated
   deliverable titles.

## Constraints
- This MUTATES live demo data (intended). Lossless rule does not apply to campaigns. Do NOT touch memory
  drawers/observations or the P1/Callie files.
- It will make real LLM calls (proposal + deliverables). That's expected.
- If a step legitimately can't complete (e.g., deliverables pipeline not seeded — the handler raises
  `campaign_deliverables_pipeline_missing`), STOP and report exactly what's missing rather than faking it.
- ruff/mypy only matter if you add code; this is mostly an execution script. Keep any helper script out of
  the committed tree (or put it under scripts/ if reusable) — coordinate with Lead.

## Acceptance
Candidate 18 is fully initiated from the HB27 signal with a brief and generated deliverables, the
deliverables run succeeded, and the Campaigns surface shows one complete real campaign. Report the verified
end state to Lead.
