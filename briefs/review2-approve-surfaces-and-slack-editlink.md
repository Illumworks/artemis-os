# REVIEW2 — Multi-surface approve + "Edit in Writing Studio" links + Slack draft deep-link

**Paste-into:** Codex OR terminal Claude Lead.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. Frontend (campaign-page
approve + edit-link relabel) + a Slack message block + the human-gate caller. Touches the live
marketing surface, so a browser smoke is a hard gate.
**Target branch:** `worker/review2-approve-surfaces-slack`
**FIRES AFTER `REVIEW1` MERGES** — it depends on REVIEW1's draft deep-link URL
(`#writing-studio?draft=<deliverable_id>` + the `writingStudioDraftHref` helper). Do not start until
REVIEW1 is on main.
**No migration.** Touches `public/js/features/marketing-os.js`, `public/js/core/api.js`,
`artemis/pipelines/node_executors/human_gate_executor.py`,
`artemis/integrations/slack/messages.py`.
**Authoritative finding:** internal-review audit (2026-06-02) + Jon's locked design.
**LOC cap:** ~300.
**Priority:** HIGH — completes the internal review/approve flow.

---

## The locked design (Jon)
- **Editing happens ONLY in Writing Studio** (REVIEW1 made save work + added the deep-link URL).
- **Approval can happen in three places:** the Gate-2 approval drawer (exists), the **marketing
  campaign page** it's assigned to (HOLLOW — build), and **Slack** (approve/reject buttons exist).
  All three funnel through the ONE decision path: `POST /api/approvals/{id}/decision`
  (`decideApprovalApi`, `api.js:2045`) → gate resume. Do NOT invent a parallel approve path.
- **Edit links** in the approval drawer AND the campaign page deep-link to the draft's **Writing
  Studio page** (using REVIEW1's `writingStudioDraftHref(deliverable_id)`).
- **No outbound email** — this is purely internal review/edit/approve.

## Scope

### Part A — Approve on the campaign page
- On the marketing campaign detail page (`marketing-os.js`, `_renderWritingStudioSection` ~line 544
  / the campaign detail tabs), when the campaign has a **pending `content_draft` approval**, surface
  it with **Approve / Reject / Request revision** controls that call `decideApprovalApi(approvalId,
  {decision})` — the SAME endpoint the Approval Queue uses (`marketing-os.js:2496-2547`). On success:
  reflect the new state + toast; the pipeline resumes via the existing decide→resume path.
- Find the pending approval for the campaign (by candidate_id → the `content_draft` approval whose
  `pipe4_context.candidate_id` matches, status pending). Reuse existing list/serialization.
- Idempotent UX: already-decided → disable/hide the controls.

### Part B — "Edit in Writing Studio" deep-links (relabel + target the specific draft)
- In the **Gate-2 approval drawer** (`_renderPipe4ApprovalCard` ~`marketing-os.js:1917`) and on the
  **campaign page**, render an **"Edit in Writing Studio"** link that uses
  `writingStudioDraftHref(deliverableId)` (REVIEW1) → opens THAT specific deliverable's WS page.
- Replace the old "Open draft →" label with "Edit in Writing Studio". **Critically: target the
  specific deliverable being reviewed**, not `latestDraftId` (the campaign-page button currently
  opens the latest draft — fix it to the reviewed deliverable).
- Keep using a real URL (REVIEW1) so it's reload-safe + shareable, not the consume-once
  `localStorage` handoff. (The handoff can stay as a fallback but the URL is canonical.)

### Part C — Slack DM gets a per-draft "Edit in Writing Studio" button
- `build_approval_dm_blocks` (`artemis/integrations/slack/messages.py:90-116`) currently renders
  Approve / Reject / "View in Artemis →" (a generic `/approvals` link). For `content_draft` gates,
  **add an "Edit in Writing Studio" button** linking to
  `{app_base_url}/#writing-studio?draft=<deliverable_id>` (REVIEW1 format).
- The caller `human_gate_executor.py:113` must **pass `app_base_url`** (today it's omitted, so even
  the generic link is relative) and the **deliverable id** — available in
  `pipe4_ctx["deliverable_ids"]` (`human_gate_executor.py:423`). Use the first/primary deliverable
  id for the link (note the choice if multiple).
- Keep Approve/Reject buttons (Slack approve still works via the existing callback). The new button
  is additive. Non-fatal if Slack/app_base_url unavailable (mirror the existing fallback).

### Part E — Feature-flag the outbound-send path OFF (hide the stub capability)
The outbound-send capability (contacts → outbox → "Send to N recipients") from CMP-SEND-2 is NOT
the current workflow (internal review/edit/approve only). Keep all code + tables, but gate the whole
path behind a single feature flag, **default OFF**, so operators don't see or trigger it and approve
doesn't churn invisible send-queue state.
- Add one flag (e.g. `config.ARTEMIS_OUTBOUND_SEND_ENABLED`, default `False`; surface to the
  frontend via the existing config/bootstrap channel if there is one, else a JS const).
- **When OFF:**
  - The Gate-2 approve hook in `approvals.py` `_decide_content_draft_approval` **skips
    `enqueue_send_for_deliverable`** entirely — approve → deliverable `approved` (terminal again),
    NO `campaign_sends` row, NO `queued_for_send` transition. (The `approved → queued_for_send`
    transition stays defined in the state machine but is simply not exercised.)
  - The shell-level **Outbox nav entry** (`MARKETING_OUTBOX_VIEW`) and the **per-campaign Outbox
    tab** are not rendered.
  - `POST /api/marketing/sends/{id}/send` and the sends-list endpoints can stay registered (dormant)
    — just unreachable from the UI.
- **When ON:** everything behaves exactly as CMP-SEND-2 shipped (re-enable is one flag flip).
- Do NOT drop the tables, endpoints, contacts substrate, or `campaign_sends` — dormant, not deleted
  (lossless + future re-enable).
- Update the CMP-SEND-2 tests that assert the approve→enqueue behavior to run under flag-ON (or
  set the flag in those tests), so they still pass; add a flag-OFF test: approve → `approved`, no
  `campaign_sends` row.

### Part D — Tests + smoke
- Backend: a test that the `content_draft` approval DM blocks include an "Edit in Writing Studio"
  action with the correct `#writing-studio?draft=<id>` URL when `app_base_url` + deliverable id are
  present. (Extend the slack messages test if one exists.)
- Frontend: `node --check`; browser smoke (Part A approve + Part B edit-link).

## Files owned
- EDIT: `public/js/features/marketing-os.js` (campaign-page approve + edit-link relabel/targeting +
  hide per-campaign Outbox tab when flag OFF)
- EDIT: `public/js/core/api.js` (if a wrapper is needed)
- EDIT: `public/js/core/navigation.js` (hide `MARKETING_OUTBOX_VIEW` nav entry when flag OFF — note:
  REVIEW1 also edited this file; rebase/merge cleanly on top of REVIEW1)
- EDIT: `artemis/marketing/routes/approvals.py` (flag-gate the enqueue-on-approve hook — Part E)
- EDIT: `artemis/config.py` (the `ARTEMIS_OUTBOUND_SEND_ENABLED` flag, default False) + bootstrap if
  the frontend needs it
- EDIT: `artemis/integrations/slack/messages.py` (content_draft edit button)
- EDIT: `artemis/pipelines/node_executors/human_gate_executor.py` (pass app_base_url + deliverable id)
- EDIT/NEW: slack messages test + flag-OFF approve test

## Acceptance criteria
1. **Campaign-page approve (browser smoke):** a campaign with a pending content_draft draft shows
   Approve/Reject/Request-revision on its page → approve → approval approved + run resumes (same as
   the drawer). **Paste console + DB before/after.**
2. **Edit links:** the drawer + campaign page show "Edit in Writing Studio" → clicking opens THAT
   deliverable's WS page via `#writing-studio?draft=<id>` (reload-safe). **Paste a description.**
3. **Slack DM:** for a content_draft gate, the DM blocks include the "Edit in Writing Studio" button
   with the right URL. **Paste the rendered blocks (test output) +, if a live Slack integration is
   configured, a screenshot/description; else note the in-app fallback.**
4. **Outbound path hidden (flag OFF):** no Outbox nav entry, no per-campaign Outbox tab; approving a
   draft → deliverable `approved`, NO `campaign_sends` row created, NO `queued_for_send`. Flipping
   the flag ON restores the CMP-SEND-2 behavior. **Paste DB proof (approve with flag OFF → no send
   row) + a note that nav/tab are gone.**
5. `node --check` + the slack test + flag-OFF approve test + `./scripts/check.sh` (j5b exempt). **Paste.**
6. **COMMIT on `worker/review2-approve-surfaces-slack`. Local git only.** Message ends
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Hard constraints
- **All approve paths call `POST /api/approvals/{id}/decision`** — no parallel approve logic.
- **Editing only links out to Writing Studio** — never add an inline edit-save on the campaign page
  or in the drawer.
- **No outbound email.** **Lossless. Local-only git. No new deps.**
- REVIEW1 must be merged first (deep-link URL dependency).

## Report-back format
```
REVIEW2 — approve surfaces + slack edit-link report
1. Commit / branch
2. Campaign-page approve: how you found the pending approval + the controls + endpoint reused
3. Edit-link: the relabel + how you targeted the specific deliverable (not latest)
4. Slack DM: the new button + URL + how app_base_url/deliverable_id flow in
5. Smokes: campaign approve (DB before/after) + edit-link open + slack blocks
6. check.sh
7. Surprises
```
