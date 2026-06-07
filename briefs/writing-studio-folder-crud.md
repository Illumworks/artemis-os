# Brief — Writing Studio folder CRUD routes (P0, the folder-delete 405)

**Type:** P0 — missing backend routes (frontend calls them, gets 405). **For terminal to delegate** (Sonnet
worker). Own worktree, cwd inside, branch `worker/ws-folder-crud` off `main`. Own test DB. Do NOT merge — report.

## Problem (confirmed live)
The Writing Studio folder create/rename/delete buttons call routes that don't exist → **405**:
- `createWritingFolderApi` → `POST /api/writing-studio/folders`
- `updateWritingFolderApi` → `PUT /api/writing-studio/folders/{id}`
- `deleteWritingFolderApi` → `DELETE /api/writing-studio/folders/{id}`  ← Jon's "error when you delete a folder"
`artemis/marketing/routes/writing_studio.py` has NO folder routes (only `/drafts/*`, `/training-candidates/*`,
`/seed/*`, `/overview`). Folders currently only get auto-derived from campaigns (`backfill_campaign_folders`),
so the management buttons are orphaned.

## Implement (in writing_studio.py, prefix `/api/writing-studio`)
1. `POST /folders` — create a folder `{name}` → returns the folder (serialized like `/overview` does via
   `_serialize_folder`). Repo fn `create_folder`.
2. `PUT /folders/{folder_id}` — rename `{name}` → returns updated folder. Repo fn `update_folder` (404 if missing).
3. `DELETE /folders/{folder_id}` — delete the folder. **LOSSLESS: do NOT delete drafts.** Unset/clear the
   `folder_id` on drafts in that folder (they move to "All drafts" — matches the UI confirm copy "Drafts
   will remain available in All drafts"). Repo fn `delete_folder` (404 if missing). Return 200/204.
   The drafts' folder assignment lives in JSONB `metadata.folder_id` (see `_get_meta(d,"folder_id")` in the
   overview's filter logic) — clear it there.

## Decision to make (flag in report, pick the sane default)
Folders are also **auto-derived from campaigns** (`backfill_campaign_folders` builds folder names from
candidate names). Decide + document: a deleted *campaign-derived* folder shouldn't silently reappear on the
next backfill. Sane default: backfill only creates a folder if one doesn't already exist AND wasn't
explicitly deleted — OR scope user-delete to user-created folders and disallow/relabel delete on
campaign-derived ones. Don't guess silently — pick one, note it, keep it lossless (drafts always preserved).

## Verify (live — assert the effect)
- Create a folder → appears in `/overview`. Rename → name updates. Delete → folder gone, **its drafts still
  exist** (moved to All drafts, `folder_id` cleared), no 405/error. Re-run overview confirms.
- Backfill behavior per the decision above (deleted campaign folder doesn't silently respawn).
- Existing WS tests pass. ruff + mypy clean.

## Constraints
Lossless (drafts never deleted; folder rows can be hard-deleted since they're org-only, but drafts are
preserved). Reuse the WS repo patterns. Org dep rule. Isolated worktree. Do NOT merge — report branch + SHA
+ how create/rename/delete + draft-preservation were verified. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
