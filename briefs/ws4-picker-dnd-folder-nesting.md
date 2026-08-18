# Worker Brief — WS Backlog #4: drafts picker drag-drop + folder nesting

**Owner:** Codex (FE; tiny backend confirm if needed). **Lead:** Artemis (Opus) drives the live browser
verification + merges (Codex builds; Lead proves the effect). **Do ws3 first, then this** — both touch
composer-v5.js, so run them as separate branches one at a time.
**Branch:** `worker/ws4-picker-dnd`. **Status:** READY.
**Do-NOT-merge-report** in an isolated worktree; Lead merges after a live smoke.

## Why
The Finder-style drafts picker can't drag-drop drafts into folders, nor nest folders inside folders.

## Good news — the backend already supports this (FE-mostly task)
- `writing_folders` already has **`parent_folder_id`** (with `idx_writing_folders_parent`) → folder nesting
  is supported at the data layer.
- Move endpoints already exist: **`PUT /api/writing-studio/drafts/{id}`** sets `folder_id` (draft → folder),
  and **`PUT /api/writing-studio/folders/{id}`** can set `parent_folder_id` (folder → folder).
- So this is drag-drop wiring against existing endpoints. **Confirm** the two PUT endpoints accept
  `folder_id` / `parent_folder_id` respectively; if a field isn't wired through, that's a tiny backend add
  (flag to Codex), not a redesign.

## Scope (FE)
1. **Make picker rows draggable** — both drafts and folders, in the composer-v5 drafts picker.
2. **Drop targets** — folders (and the root / "Ungrouped").
   - draft → folder: `PUT /drafts/{id}` `{ folder_id }`. Drop on root → `folder_id = null`.
   - folder → folder: `PUT /folders/{id}` `{ parent_folder_id }`. Drop on root → `parent_folder_id = null`.
3. **Render nesting** — show nested folders as an indented tree; respect the existing expand/collapse state.
4. **Guards** — prevent dropping a folder into **itself or one of its own descendants** (cycle). Ignore no-op
   drops. Give a clear hover/drop affordance.
5. Optimistic UI is fine, but re-sync from the API response so a reload reflects reality.

## Constraints
- FE in composer-v5.js + its CSS; do not touch the selection-toolbar logic (hands-off — see SESSION-STATE).
- Reuse the existing folder-CRUD + draft-update endpoints; coordinate any backend gap with Codex rather than
  inventing a new endpoint.
- Match surrounding picker code/style.

## Acceptance (Lead verifies LIVE — assert the EFFECT)
In a real browser: drag a draft into a folder → reload → it persists in that folder. Nest a folder inside
another → reload → persists as nested. Drop on root → un-nests. Attempting to nest a folder into its own
descendant is prevented. Verified with screenshots.
