# Build brief — Campaign workspace pass: real assets, review clarity, two-way draft↔campaign linking

**Agent:** terminal (FE — `marketing-os.js` campaign view + `composer-v5.js` composer actions) + backend
(`artemis/marketing/routes/` for link/unlink/approve). **Branch:** `worker/campaign-workspace` off **current
`main` — START ONLY AFTER `worker/composer-phase-b` is merged** (both touch `composer-v5.js`; sequence to
avoid conflicts; confirm with Lead before branching). **Own git worktree, cd inside it, own test DB
`artemis_test_campaignws`.** **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`.

**⚠️ HANDS-OFF (composer-v5.js):** do NOT touch the selection-toolbar logic
(`updateSelectionState`/`positionNearSelection`/`showSelToolbar`/`hideSelToolbar`/`dispatchTransaction`/
`handleOutsidePointerDown` + the `.cv5-paper`/`.ProseMirror` padding). You add NEW composer header/actions
only.

## Context
The campaign workspace (the campaign detail view in `marketing-os.js` — Assets tab + the "Content Review
Pending" card) is under-built. A draft↔campaign link already exists via `CampaignDeliverable.candidate_id`
(auto-created drafts are linked to the campaign's candidate). This pass makes the link visible, editable both
ways, and the review state clear.

## 1. Asset rows show the REAL asset, not "Draft"
In the campaign Assets tab, each deliverable row currently renders the literal word "Draft". Render the
asset's **title** + **type** (email / one-pager / etc. from `deliverable_metadata.title` /
`assetType`/`asset_type`) + its **status** (draft_ready / in review / approved / queued_for_send / sent).
Fall back to a sensible label only if title is genuinely empty.

## 2. Wire the per-row buttons → Open + Remove from campaign
The right-side row controls do nothing. Make each row actionable:
- **Open** → open that draft in Writing Studio (navigate to the composer for that draft id).
- **Remove from campaign** → unlink the deliverable from this campaign (lossless — set it to the detached/
  placeholder state, e.g. reuse the templates placeholder-candidate path used for standalone drafts; never
  delete the draft). Confirm before unlinking. Refresh the list.
- (Clicking the row body can also open it.)

## 3. "Content Review Pending" card — name it, show all, current-only
Today it's one vague card. Rework so it:
- Names **which asset(s)** are pending review (title + type), not just "Content review pending".
- Shows **every** current pending review for this campaign (a list if multiple), not just the first.
- Only reflects the **current** version of each deliverable — never older/superseded versions.
- Keeps the Approve / Request revision / Reject actions per item, each acting on that specific asset.

## 4. Two approval paths — add "Approve for campaign" in the composer
A draft in review can currently only be approved from the Approval Queue. Add an **"Approve for campaign"**
action in the Writing Studio composer (header/actions menu) that appears when the open draft is pending a
content review, and approves it via the SAME backend path the Approval Queue uses (don't fork the approve
logic). After approving, reflect the new status. Request-revision / Reject can stay queue-only for now.

## 5. Two-way draft↔campaign linking
- **From the campaign (Assets tab footer):**
  - **"Add new asset"** → create a fresh blank draft already linked to THIS campaign and open it in the
    composer (reuse the blank-draft path; set its `candidate_id` to this campaign's candidate).
  - **"+ Link asset"** → pick an existing (unlinked or any) draft and attach it to this campaign (set its
    `candidate_id`). Refresh the list.
- **From the composer (Writing Studio):** add an **"Attach to campaign"** action (header/actions menu) that
  lets the user link the CURRENT draft to a chosen campaign (set `candidate_id`). Shows the current campaign
  if already linked.
- **Backend:** small endpoints to attach/unlink a deliverable to/from a campaign candidate (additive,
  lossless; reuse the candidate/placeholder substrate — don't invent a new schema). A list-campaigns endpoint
  for the picker if one isn't already available.

## Acceptance (verify the EFFECT — browser + screenshots)
- Assets tab shows real titles/types/status (not "Draft"); Open navigates to the draft; Remove unlinks it
  (draft survives, list refreshes).
- Content Review Pending names the asset(s), lists multiple when present, current-version-only.
- "Approve for campaign" in the composer approves a pending draft (prove the status flips + it leaves the
  pending list), matching the queue path.
- Add new asset → blank draft linked + opened. + Link asset → existing draft attached. Attach-to-campaign
  from the composer links the current draft. Prove the `candidate_id` link in the DB each way.
- Selection toolbar still works (regression check — untouched). No console errors. `./scripts/check.sh` for
  touched Python (note PRE-EXISTING separately).

## Constraints
Lossless (unlink = detach, never delete; drafts/versions preserved). Reuse the existing approve path, the
candidate/placeholder substrate, blank-draft creation, and composer header/actions plumbing — don't fork.
Isolated worktree + own test DB. **Hands-off the selection toolbar.** **Do NOT merge** — report branch + SHA
+ worktree + screenshots + the DB link proofs. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
