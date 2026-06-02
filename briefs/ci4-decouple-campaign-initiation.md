# CI4 — Decouple campaign initiation from the discovery run (the keystone fix)

**Paste-into:** Codex.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort **high**. This is core-flow
architecture: a new pipeline, a migration, executor candidate-resolution, an endpoint rewrite, and
a real end-to-end test. Use the flagship at high effort — do NOT downgrade.
**Target branch:** `worker/ci4-decouple-initiation`
**Fires:** now (CMP1+MD1 already merged). **Has a migration** — head is `0058`, yours is `0059`.
**Authoritative design:** `docs/campaign-initiation-and-district-design.md` § "Stream 3: decouple
campaign initiation from the discovery run". Read it first — it explains *why*.
**LOC cap:** ~450.
**Priority:** CRITICAL — campaign confirm is currently unusable for any real (cross-run) campaign.

---

## Why this exists (read the design doc § Stream 3 for the full story)

The confirm endpoint `POST /api/marketing/campaigns/{candidate_id}/initiate` fails with
`gate_node_not_found` because it tries to **resume one pipeline run paused at a
`gate_campaign_initiation` human-gate**. But Stream-2 campaign candidates **span multiple discovery
runs** (their signals come from different scout runs), so there is no single run to resume. Proven
live: candidate 5's signals came from runs `c9b57dbf` + `48c198d6`, both still parked at
`gate_1_signals_inbox`; the confirm transaction rolls back and the campaign never initiates.

**The fix:** the campaign candidate is a first-class entity. The discovery pipeline ends at Gate-1
(+ proposal). Confirm = `initiate_campaign()` + **start a fresh on-demand deliverable run keyed to
the candidate**. No discovery-run resume.

## What exists already (verify, don't assume — file:line from a fresh read)

- `artemis/marketing/routes/initiation.py` — the `POST /{candidate_id}/initiate` handler (~155)
  and the initiation-context `GET` (~100). The broken coupling: `_resolve_candidate_run_id` (~297),
  `_prepare_pipeline_resume` import (line 32), the `_dispatch_execution(pipeline_run_id)` of the OLD
  run (~243), and the `gate_timeout_*` scheduler removal (~239). **These come out of the confirm path.**
- `artemis/marketing/repository.py` — `initiate_campaign` (~426, only flips candidate fields),
  `list_run_candidates` (~615, joins `signal_queue.pipeline_run_id` → candidate),
  `propose_campaign_initiation` lives in `brief_assembler.py` (~435).
- `artemis/pipelines/seeds/marketing_pipeline.py` — builds `marketing.main`. The linear list (~146)
  currently includes `gate_campaign_initiation`, `content_asset_selector`,
  `content_writing_studio_adapter`; deliverable nodes + `gate_2_approval_drawer` are appended after.
- `artemis/pipelines/node_executors/agent_executor.py` — `_deliverable_enabled_for_run` and the
  deliverable-node candidate resolution (~87–106) use `list_run_candidates(run_id, initiated_only=True)`.
- `artemis/pipelines/routes.py` — `create_pipeline_run` usage (~401) + `_dispatch_execution` (~122).
  `PipelineRun.metadata_` exists; **there is NO `target_candidate_id` column yet.**
- `artemis/marketing/writing_studio/invoke.py` — `create_draft_from_candidate(session,
  candidate_id, ...)` takes candidate_id directly (no run coupling). Good.
- `artemis/pipelines/node_executors/human_gate_executor.py` — gate suspend creates an `Approval`
  row (`subject_id = f"{run_id}:{node_id}"`) and stores `status="suspended"` in node_states.

---

## Scope

### Part A — Migration 0059: explicit candidate↔run link
- Add nullable column `target_candidate_id BIGINT` to `pipeline_runs`, FK →
  `campaign_candidates(id)`, `ON DELETE SET NULL`, with an index. Down-migration drops both.
- Add the field to the `PipelineRun` model (`artemis/pipelines/models.py`) as
  `target_candidate_id: Mapped[int | None]`. Keep it out of any required-field schema.
- `revision="0059"`, `down_revision="0058"`. **Run `git diff --staged` before committing** to
  confirm the revision strings are staged (per CLAUDE.md migration-renumber lesson).

### Part B — New pipeline `marketing.campaign_deliverables`
Add a builder + seed (same file `seeds/marketing_pipeline.py`, or a sibling
`seeds/campaign_deliverables_pipeline.py` — your call; wire it into whatever runs the seeds, e.g.
`scripts/seed_*` / the seed registry). Pipeline id `marketing.campaign_deliverables`, `status`
active, **manual trigger** (`trigger_manual` node, NOT scheduled — it's started on demand):

```
trigger_manual → content_asset_selector → content_writing_studio_adapter
  → deliverable_<slug> (one per ACTIVE DeliverableType, same _deliverable_node config as today)
  → gate_2_approval_drawer
```
- Reuse the existing node builders (`_deliverable_node`, the `gate_2_approval_drawer` config block)
  from `marketing_pipeline.py` so the deliverable + gate behavior is identical to today.
- Deliverable types are registry-driven exactly as in `marketing.main` (query active
  `DeliverableType`, one node each).

### Part C — Trim `marketing.main` to end at the proposal
- Remove `gate_campaign_initiation`, `content_asset_selector`, `content_writing_studio_adapter`,
  the `deliverable_*` nodes, and `gate_2_approval_drawer` from `marketing.main`. New tail:
  `… → gate_1_signals_inbox → content_brief_assembler (propose_initiation=True) → END`.
- Keep `content_brief_assembler` with `propose_initiation=True` (it clusters approved signals +
  writes the proposal — a pre-warm; no longer load-bearing for confirm).
- Re-seed both pipelines (`uv run python -m ...` or the seed script). Note new node/edge counts.

### Part D — Deliverable nodes resolve candidate via `target_candidate_id` first
- In `agent_executor.py`: when a node has a `deliverable_type_slug` (or is asset_selector /
  writing_studio_adapter and needs the candidate), resolve the candidate as:
  1. If `run.target_candidate_id` is set → use that candidate directly.
  2. Else fall back to `list_run_candidates(session, run_id, initiated_only=True)` (legacy path).
- Update `_deliverable_enabled_for_run` the same way: prefer `run.target_candidate_id`'s
  `deliverable_types_json`; fall back to the signal-join. Behavior for an unknown/missing candidate
  stays the same (the existing "No initiated candidate" failure / "skipped" semantics).
- Make sure `content_asset_selector` + `content_writing_studio_adapter` get the resolved
  `candidate_id` (they call `create_draft_from_candidate(candidate_id=…)`). Trace how they get it
  today and route the `target_candidate_id`-resolved id through the same seam.

### Part E — Rewrite the confirm endpoint (`initiation.py`)
`POST /{candidate_id}/initiate` new flow:
1. Load candidate; require `initiation_proposal_json` present (existing 409 if missing).
2. Validate `deliverable_type_slugs` are active (existing validation).
3. `await initiate_campaign(session, candidate_id, …)` (existing call, unchanged).
4. **Create a `marketing.campaign_deliverables` run** via `create_pipeline_run(... pipeline_id=
   "marketing.campaign_deliverables", status="queued", trigger="manual",
   triggered_by=actor, target_candidate_id=candidate_id)`.
5. `await session.commit()`.
6. `_dispatch_execution(new_run_id)` (dispatch the NEW run, out-of-process as today).
7. Return the serialized candidate **plus** `{"deliverableRunId": new_run_id}`.
- **Delete** `_resolve_candidate_run_id`, the `_prepare_pipeline_resume` import + call, the
  `gate_timeout_*` scheduler-removal block, and the `_resolve_pipeline_run_id`-driven resume. The
  `pipelineRunId`/`gateNodeId` fields in the GET context can stay or go — if you keep `pipelineRunId`
  for display, make it clearly the discovery run, not a resume target. **No gate resume anywhere in
  confirm.**
- `initiate_campaign` raising "already initiated" → existing 409. Idempotency: a second confirm on
  an already-initiated candidate → clean 409 (do NOT start a second deliverable run).

### Part F — Lazy proposal generation (robustness)
- In the initiation-context `GET` handler: if `candidate.initiation_proposal_json` is absent,
  generate it on demand via `propose_campaign_initiation(session, candidate_id, model_adapter=…)`
  (the same call the e2e used), persist, then read it back. So the form works even if the discovery
  pipeline never ran `content_brief_assembler` for this candidate. If generation fails, return a
  clear error (don't 500 opaquely) — surface "proposal could not be generated" with the reason.

### Part G — Tests (`artemis/marketing/tests/test_ci4_decouple_initiation.py`)
1. **Confirm with no paused gate succeeds.** Create a candidate with a proposal (no pipeline run at
   any initiation gate) → POST initiate → 200, candidate `initiated_at` set, AND a
   `marketing.campaign_deliverables` run exists with `target_candidate_id == candidate.id`.
2. **Cross-run candidate.** Cluster two signals whose `pipeline_run_id`s differ into one candidate →
   confirm → succeeds (this is the exact case that broke before).
3. **Deliverable node resolves via `target_candidate_id`** (unit): a run with `target_candidate_id`
   set resolves that candidate without any signal having that run's id; `_deliverable_enabled_for_run`
   returns enabled/skip correctly from the candidate's `deliverable_types_json`.
4. **Idempotency:** second confirm on an initiated candidate → 409, no second deliverable run.
5. **Lazy proposal:** GET initiation-context on a candidate with no `initiation_proposal_json` →
   proposal generated + persisted (mock the model adapter so it's deterministic + offline).
6. **Legacy fallback:** deliverable node with NO `target_candidate_id` but signals joined to the run
   still resolves via `list_run_candidates` (don't regress the old path).

Mock the LLM (model adapter) and the External Writing Studio — tests must run offline/deterministic.

---

## Files owned
- NEW: `alembic/versions/0059_ci4_run_target_candidate.py`
- EDIT: `artemis/pipelines/models.py` (+`target_candidate_id`)
- EDIT: `artemis/pipelines/seeds/marketing_pipeline.py` (trim main + new pipeline builder/seed)
  (or NEW sibling seed file + registry wiring)
- EDIT: `artemis/pipelines/node_executors/agent_executor.py` (candidate resolution)
- EDIT: `artemis/marketing/routes/initiation.py` (confirm rewrite + lazy proposal)
- POSSIBLE EDIT: `artemis/pipelines/repository.py` if `create_pipeline_run` needs the new kwarg
  passthrough (it uses `**kwargs`, so likely free — verify).
- NEW: `artemis/marketing/tests/test_ci4_decouple_initiation.py`

## Acceptance criteria
1. `uv run alembic upgrade head` clean; `alembic heads` shows `0059`. **Paste.**
2. `uv run pytest artemis/marketing/tests/test_ci4_decouple_initiation.py -v` — all pass. **Paste.**
3. **Real end-to-end (the proof):** with the app running + both pipelines re-seeded — approve two
   qualified signals from DIFFERENT discovery runs (or reuse candidate 5 if still present) →
   `GET /api/marketing/campaigns/{id}/initiation-context` (proposal present, lazily generated if
   needed) → `POST /api/marketing/campaigns/{id}/initiate` → **200**, candidate initiated, a
   `marketing.campaign_deliverables` run created with `target_candidate_id` and dispatched; the run
   reaches `content_writing_studio_adapter` → a `campaign_deliverables` row is created → run
   suspends at `gate_2_approval_drawer` with a `content_draft` approval row. **Paste the curl
   outputs + DB state (campaign_candidates.initiated_at, the new pipeline_runs row with
   target_candidate_id + status, campaign_deliverables row, approvals row).**
4. `./scripts/check.sh` (j5b Jira flake known-exempt) + `git diff --stat` + `git log --oneline -1`. **Paste.**
5. **COMMIT on `worker/ci4-decouple-initiation`. Local git only, no push.** End the commit message
   with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Hard constraints
- **No gate resume in the confirm path.** The campaign deliverable run is a NEW run, not a resume.
- **`initiate_campaign` must commit independently** of any pipeline dispatch — a dispatch hiccup
  must NOT roll back the initiation (the old transactional coupling is the bug).
- **Registry-driven deliverables** — one node per active `DeliverableType`, no hardcoding.
- **Lossless / append-only** — no DELETE of candidates/signals/observations.
- **Dependencies:** add none. (Org rule: nothing < 7 days old; not relevant here.)
- **Local-only git.** Migration `git diff --staged` check before commit (CLAUDE.md lesson).

## Report-back format
```
CI4 — decouple campaign initiation report
1. Commit / branch / migration (0059)
2. LOC per file
3. New pipeline: node + edge count; trimmed marketing.main: new node + edge count
4. Candidate resolution: how asset_selector + writing_studio_adapter get target_candidate_id
5. Test pass count (esp. #1 confirm-with-no-gate + #2 cross-run)
6. Real e2e: paste curls + DB state (candidate, new run w/ target_candidate_id, deliverable, approval)
7. check.sh summary
8. Surprises — esp. how the deliverable nodes consume the candidate id + any seed-registry wiring
```
