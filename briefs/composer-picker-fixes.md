# Build brief — Composer picker fixes: New-draft no-op + add delete-folder

**Agent:** terminal (composer FE — owns `composer-v5.js`; small backend tweak allowed). **Branch:**
`worker/composer-picker-fixes` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_pickerfix`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md` + the current
`composer-v5.js` (Stage-3 picker). Two reported bugs (Jon, live demo).

## Bug 1 — "+ → New draft" does nothing
Today the New-draft handler (`composer-v5.js` ~line 1223) creates the draft from the **current draft's
`candidate_id`**, and bails when there isn't one (and Stage 3 noted a true blank-draft path was left out of
scope). Result: clicking New draft silently no-ops on drafts without a candidate, and even when it "works"
the new draft isn't reliably opened.
**Fix:** make New-draft reliably create a genuinely blank draft and **open it**, independent of the current
draft. This likely needs a small **backend tweak**: a create-draft path that doesn't require a real
`candidate_id` (e.g. reuse the templates work's `_get_or_create_template_workspace_candidate` placeholder
candidate in `invoke.py`, or allow `POST /drafts` with no candidate by attaching the same placeholder). On
success: refresh the picker + select/open the new draft in the composer. Show an error toast if it truly
fails (never a silent no-op). Keep it lossless (additive).

## Bug 2 — no delete-folder in the picker
The picker has new-folder but no delete. The backend folder-delete EXISTS
(`DELETE /api/writing-studio/folders/{id}`, tombstone/soft — lossless). **Fix:** add a delete affordance on
each folder row in the picker (e.g. a small ⋯ or trash on hover) → confirm → `DELETE` the folder → refresh
the tree. Drafts in a deleted folder must NOT be lost (they fall back to Ungrouped/their candidate folder per
existing behavior — verify). Don't allow deleting the synthetic "All drafts"/"Ungrouped" groups.

## Build on what exists (don't fork)
Reuse `createWritingDraftApi` / folder CRUD helpers in `api.js` (add a delete-folder helper if missing).
Reuse the picker render + the existing folder CRUD backend. For the blank-draft path, reuse the templates
placeholder-candidate substrate (`invoke.py`) rather than inventing a new deliverables schema.

## Acceptance (verify the EFFECT — browser)
- New draft: from ANY open draft (incl. one without a candidate), "+ → New draft" creates a blank draft AND
  opens it in the composer (prove a fresh GET shows the new draft; the editor shows it empty/ready). No
  silent no-op.
- Delete folder: create a folder, file a draft in it, delete the folder → folder gone from the tree, the
  draft survives (Ungrouped), no data loss. Synthetic groups aren't deletable.
- No console errors; claim flags / comments / pagination / autosave still work. `./scripts/check.sh` for any
  touched Python (note PRE-EXISTING failures separately). Browser-eyeball + screenshots.

## Constraints
Lossless (folder delete is tombstone; drafts never lost; blank-draft is additive). Reuse existing backends +
the templates placeholder-candidate path; don't fork draft creation. Migration only if genuinely needed
(prefer reusing the placeholder candidate — no schema change). Isolated worktree + own test DB. **Do NOT
merge** — report branch + SHA + worktree + browser smoke (new-draft-creates-and-opens; delete-folder-keeps-
drafts). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies +
merges.
