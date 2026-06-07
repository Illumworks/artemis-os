# Brief — Fix folder respawn after delete (Writing Studio) — follow-up on merged main

**Type:** P1 bugfix follow-up (folder CRUD is already merged to main; this fixes the respawn it left).
**For terminal → Sonnet worker.** Own worktree, cwd inside, branch `worker/ws-folder-respawn` off `main`.
Own test DB. Do NOT merge — report.

## Bug (confirmed live by Lead on main)
Deleting a campaign-derived folder soft-deletes it (`writing_folders.deleted_at` set ✓), but the next
`/overview` load **respawns it as a new folder** (new id, same `campaign_id`, same name) and re-stamps the
draft's `folder_id` into it. Verified: deleted folder 10 (campaign 15) → folder 14 appeared
(campaign_id=15, deleted_at NULL). So folder delete is effectively a no-op for campaign folders (the
original "folder-delete no-op" symptom).

## Root cause (precise)
`wr_repo.get_or_create_folder_by_candidate` (`artemis/writing_rules/repository.py:130`, calls
`get_folder_by_candidate` at :149): `get_folder_by_candidate` correctly filters tombstones (returns None
for a soft-deleted folder), but the "create" half then **unconditionally creates a NEW folder** — so a
tombstoned campaign folder is treated as "no folder" and recreated. It's called at **three** sites, all of
which respawn:
- `artemis/marketing/writing_studio/invoke.py:612` — backfill (`backfill_campaign_folders`)
- `artemis/marketing/writing_studio/invoke.py:391` — the campaign→WS handoff route (Worker A's path)
- `artemis/marketing/writing_studio/invoke.py:231` — the original auto-create path
Patching only the backfill leaves two silent respawns (handoff + auto-create).

## Fix
Make folder resolution **tombstone-aware** and apply it at all three call sites. Behavior:
- An ACTIVE (non-tombstoned) folder for the candidate exists → return it.
- A TOMBSTONED folder exists for the candidate → return None: **do NOT create, do NOT stamp the draft's
  folder_id** (the draft stays in All drafts — respects the delete).
- No folder row at all for the candidate → create a fresh one (first-time behavior, unchanged).

Implement as a tombstone-aware variant (or a `respect_tombstone=True` path) rather than breaking the
existing get-or-create contract; update the 3 call sites to use it and to handle the None (skip stamping
`folder_id`). **Jon-confirmed intent:** once a campaign folder is deleted, auto-paths never resurrect it;
operators can still manually re-folder via the folder routes.

## Verify (live — assert the effect)
- Delete a campaign-derived folder → GET `/overview` → **no new folder created** for that campaign (no
  respawn), and its draft stays unfoldered (folder_id cleared, lands in All drafts). Re-load again → still
  no respawn.
- Also hit the **handoff path**: "Create draft in WS" on a campaign whose folder was deleted → creates the
  draft **unfoldered** (no respawn). And the auto-create/pipeline path likewise.
- A campaign that never had a folder still gets one created (no regression).
- Drafts never lost (lossless). Existing WS tests pass. ruff + mypy clean.

## Constraints
Lossless; soft-delete tombstones preserved; no destructive migration. Reuse the existing repo patterns +
the `deleted_at` tombstone already added by migration 0069. Org dep rule. Isolated worktree + own test DB.
Do NOT merge — report branch + SHA + the live no-respawn proof across all three paths. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
