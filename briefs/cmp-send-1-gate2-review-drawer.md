# CMP-SEND-1 — Gate-2 content-draft review drawer + decide + pipeline resume

**Paste-into:** Codex OR terminal-Lead worker.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. Real wiring: the decide endpoint must update approval + deliverable state machine + workspace state AND resume a suspended pipeline run; plus the review UI. Use the flagship.
**Target branch:** `worker/cmp-send-1-gate2-review`
**Fires:** after CMP1+MD1 merges (it edits `marketing-os.js`, so don't overlap). Likely **no migration** (uses existing `approvals` + `campaign_deliverables`). If you add one, head is 0058.
**Authoritative finding:** `docs/content-path-audit-2026-06-01.md` (Stage 4 = HOLLOW). This closes the first half of the CMP-SEND stream (#108). CMP-SEND-2 = outbound send (separate; has an email-infra decision).
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~400.
**Priority:** HIGH — without this, an initiated campaign's draft dead-ends at `awaiting_approval` with no way for the operator to review/approve and no way for the pipeline to proceed.

---

## Why this exists

After campaign initiation (Stream 2), the pipeline creates a draft and reaches the
`gate_2_approval_drawer` human_gate → suspends at `awaiting_approval`. But there's **no UI to
review the draft and no endpoint to record a decision + resume the run**. The draft dead-ends.
CMP-SEND-1 builds the operator's Gate-2 review: see the draft, approve / reject / request
revision, and the pipeline proceeds.

## What exists already (build on these — verify, don't assume)

- `gate_2_approval_drawer` human_gate node (`artemis/pipelines/seeds/marketing_pipeline.py`),
  `approval_kind="content_draft"`, approvers, `timeout_hours=72`, `on_timeout="escalate"`.
- `human_gate_executor.py` — suspends the run. **VERIFY:** does it create an `approvals` row
  (kind=`content_draft`) on suspend, like Gate-1 (`signal_brief`) does? If not, that's part of
  this brief (create the approval row at suspend so there's something to render).
- `approvals` table (kind, subject_id, status, decided_by, decision_payload, pipe4_context).
- `campaign_deliverables` + `DeliverableState` (`state_machine.py`): `draft_ready →
  approved | rejected | revised`. **VERIFY:** a deliverable actually reaches `draft_ready`
  before Gate-2 (the smoke left one at `generating` — the writing_studio.enqueue →
  draft_ready transition must fire in a real run; if there's a gap, note it / fix the minimal
  wiring so a draft is reviewable).
- The draft CONTENT: `writing_studio/external.py` (stub returns a stub draft; real fetches via
  `ARTEMIS_WRITING_STUDIO_URL`). Surface whatever draft text/metadata is available.
- PIPE4 resume: `POST /api/pipeline-runs/{run_id}/resume` (the standard gate-resume path) +
  `human_gate_executor` resume logic — the decide endpoint releases the gate through this.
- The Gate-1 approval drawer / signals-inbox UI in `marketing-os.js` is the **pattern to mirror**.

## Scope

### Part A — Decide endpoint + state + resume
`POST /api/marketing/approvals/{approval_id}/decide` (or the cleanest existing approvals route):
- body `{ decision: "approved" | "rejected" | "revision_requested", reason?: str }` (Pydantic).
- Update the `approvals` row: status, decided_by (current user), decision_payload, decided_at.
- Transition the deliverable via the state machine: `draft_ready → approved | rejected |
  revised` (use `DeliverableState` + the existing transition() — don't bypass).
- Update `campaign_candidates.workspace_state` via the existing writing_studio/adapter logic
  (content_in_review → all_content_approved / revision_needed).
- **Resume the suspended pipeline run** with the gate decision injected into node_states
  (mirror how Gate-1 resume works). approved/rejected proceed per the DAG; revision_requested
  routes back appropriately (or marks the deliverable revised + holds — pick the minimal correct
  behavior + note it).
- Idempotent: deciding an already-decided approval → clear 4xx.

### Part B — Ensure there's an approval to review (if Gate-2 doesn't already create one)
If Part A's VERIFY shows Gate-2 suspension doesn't create a `content_draft` approval row, wire
it (in `human_gate_executor` or the gate node handler) so suspending creates the row with
`subject_id` = the deliverable/candidate + `pipe4_context` = draft preview + run_id. (Mirror the
Gate-1 signal_brief approval creation.)

### Part C — Gate-2 review drawer UI (`marketing-os.js`)
- A review surface listing pending `content_draft` approvals (query approvals where
  kind=content_draft, status=pending) — for each: campaign name, the **draft content/preview**,
  deliverable type, the source signal cluster (link), district context.
- **Approve / Reject / Request revision** buttons → POST decide → on success, remove from the
  list + toast + the pipeline resumes.
- Mirror the existing approval-drawer idiom. api.js wrappers for list + decide.
- Empty state when no drafts pending review.

### Part D — Tests (`artemis/marketing/tests/test_cmp_send_1_gate2_review.py`)
1. Gate-2 suspension creates a content_draft approval row (or: one exists to decide).
2. POST decide `approved` → approval status approved + deliverable `draft_ready→approved` + run resumes (gate released).
3. POST decide `rejected` → deliverable `draft_ready→rejected` + correct workspace_state.
4. POST decide `revision_requested` → deliverable `→revised` + the minimal correct hold/route behavior.
5. Decide on already-decided → idempotency 4xx.
6. Invalid decision value → self-teaching 4xx.

---

## Files owned
- EDIT/NEW: `artemis/marketing/routes/` (decide endpoint — reuse existing approvals route if present)
- EDIT: `artemis/pipelines/node_executors/human_gate_executor.py` (Gate-2 approval-row creation, if missing) + resume wiring
- EDIT: `artemis/marketing/writing_studio/adapter.py` or repository (workspace_state on decision) if needed
- EDIT: `public/js/features/marketing-os.js` (Gate-2 review drawer) + `public/js/core/api.js` (wrappers)
- NEW: `artemis/marketing/tests/test_cmp_send_1_gate2_review.py`

## Acceptance criteria
1. `pytest .../test_cmp_send_1_gate2_review.py -v` — all pass. **Paste.**
2. **Browser/flow smoke:** a campaign that reached Gate-2 shows a draft in the review drawer →
   approve → approval recorded + deliverable approved + **pipeline run resumes** (status moves
   off awaiting_approval). **Paste console + DB state before/after (approvals + deliverable + run status).**
3. Reject + request-revision paths behave correctly. **Paste.**
4. `./scripts/check.sh` (j5b Jira flake known-exempt) + `git diff --stat` + `git log --oneline -1`. **Paste.**
5. **COMMIT on `worker/cmp-send-1-gate2-review` before finishing.** Local git only, no push.

## Hard constraints
- **Use the DeliverableState machine** for transitions — don't bypass (`draft_ready → approved|rejected|revised` only).
- **Resume through the existing PIPE4 gate mechanism** — don't invent a parallel resume.
- **Operator decides; the pipeline proceeds** — no auto-approve.
- **No fabricated draft content** — surface the real draft (stub or real WS); if a deliverable isn't draft_ready, show its real status, don't fake a draft.
- **Mirror the existing approval-drawer UI**; don't overlap CMP1's Campaigns-list edits.
- **Lossless / local-only git.**

## Report-back format
```
CMP-SEND-1 — Gate-2 review drawer report
1. Commit / branch (+ migration if any)
2. LOC per file
3. Did Gate-2 already create the approval row, or did you wire it? (Part B finding)
4. Did a deliverable reach draft_ready cleanly, or was there a generating→draft_ready gap? (note)
5. Test pass count (esp. approve→resume #2)
6. Flow smoke (review → approve → run resumes; DB before/after)
7. check.sh summary
8. Surprises — esp. the human_gate resume mechanics + DeliverableState/workspace_state coupling
```
