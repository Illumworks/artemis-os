# Pipeline Delete + Archive with Confirmation

**Owner:** Codex (paste-ready, mechanical UI)
**Branch:** `codex/pipeline-delete-with-confirmation`
**LOC budget:** ~140 (honest overrun OK to ~180)
**Brief author:** Lead (Opus 4.7)
**Depends on:** PIPE1 (DELETE route exists as soft-delete via `status='archived'`). Independent of PIPE3 patch and cron UX.

## Why this brief exists

PIPE1's DELETE route is a soft-archive (status flips to `archived`). The UI doesn't surface it. Jon: "I want to be able to delete pipelines (with a confirmation are you sure screen, or archive and then delete if that makes more sense)."

After this brief: pipelines can be archived from the UI with confirmation, AND archived pipelines can be permanently deleted (or restored). Two distinct destructive actions, both surfaced cleanly.

## Scope

### In scope

1. **Delete affordance on pipeline cards in the list view:**
   - Small `…` (kebab) icon on each pipeline card, top-right
   - On click → dropdown: **Archive** / **Restore** (only if archived) / **Permanently delete** (only if archived)
   - Hover-only visibility OK if cards are dense

2. **Archive flow:**
   - Click "Archive" → confirmation dialog: "Archive {pipeline name}? Pipelines in archive are paused and hidden from default list but can be restored."
   - Confirm → calls existing `DELETE /api/pipelines/{id}` (which is soft-delete to status='archived')
   - Toast: "Pipeline archived"
   - Pipeline disappears from default list view (filter excludes archived by default)

3. **Restore flow:**
   - Visible only when filter shows archived pipelines
   - Click "Restore" → no confirmation (low-risk)
   - PATCH `/api/pipelines/{id}` with `status='active'`
   - Toast: "Pipeline restored"

4. **Permanently delete flow:**
   - Visible only when filter shows archived pipelines
   - Click "Permanently delete" → STRONG confirmation dialog: "Permanently delete {pipeline name}? This cannot be undone. All run history will be lost."
   - User must type the pipeline name to confirm (anti-fat-finger pattern)
   - On confirm → backend route needed: `DELETE /api/pipelines/{id}/permanent` (hard delete via SQL DELETE)
   - Toast: "Pipeline deleted permanently"
   - Pipeline removed from all list views

5. **Filter chip in list view:**
   - Pill toggle: **Default** (excludes archived) / **Include archived** / **Only archived**
   - Default for new users: "Default"
   - State persists in localStorage key `artemis.pipelines.archived-filter`

6. **Backend additions:**
   - `DELETE /api/pipelines/{id}/permanent` route (new) — hard delete; requires pipeline to be already archived (returns 409 if status != 'archived'). Pre-condition prevents accidental hard-delete of active pipelines.
   - `PATCH /api/pipelines/{id}` with `{status: 'active'}` route — verify route accepts this (PIPE1 likely does already; just confirm).
   - The existing `DELETE /api/pipelines/{id}` (soft delete) stays unchanged.

7. **Tests:**
   - Archive flow: confirmation dialog appears, click confirm → pipeline marked archived
   - Restore flow: button visible only when filter shows archived; click → pipeline back to active
   - Permanent delete: confirmation requires typing pipeline name; cannot delete non-archived; works when archived
   - Filter chip persists in localStorage
   - Tests for the new `/permanent` route + 409 when not archived

### Out of scope

- Bulk delete/archive (multi-select). One pipeline at a time.
- Trash/recycle bin with auto-cleanup after N days. Manual permanent-delete only.
- Audit log of who deleted what. Just operator action; no audit yet.
- Email confirmation of permanent delete. Single-operator system.
- "Undo" toast after archive. The Restore button serves this purpose.

## Invariants

1. **Default list view shows ONLY active+paused pipelines.** Archived is opt-in via filter chip.
2. **Permanent delete requires pre-archived state.** Cannot hard-delete an active pipeline directly.
3. **Permanent delete requires typing the pipeline name** as confirmation. Prevents accidental deletion.
4. **Existing DELETE route unchanged.** Frontend now routes to /permanent for hard delete; soft delete keeps its semantics.
5. **Toast feedback on every action.** Archive / restore / permanent delete all show confirmation toast.

## Files expected

| File | LOC |
|---|---|
| `artemis/pipelines/routes.py` | ~25 delta (new /permanent route + restore check) |
| `artemis/pipelines/repository.py` | ~15 delta (hard delete function) |
| `public/js/features/pipelines.js` | ~50 delta (kebab menu, dialogs, filter chip wiring) |
| `public/css/features/pipelines.css` | ~30 delta (dialog styling, kebab button, filter chip) |
| `artemis/pipelines/tests/test_pipeline_delete.py` (new or appended) | ~50 |

**Total: ~170 LOC.** Cap 180.

## Test plan

1. **Archive active pipeline:** kebab → Archive → confirm dialog → submit → status=archived → list excludes it
2. **Show archived:** filter chip "Include archived" → archived pipeline visible
3. **Restore archived:** kebab → Restore → status=active → returns to default list
4. **Permanent delete (happy path):** must be archived first; kebab → Permanently delete → name-type confirmation → submit → row deleted from DB
5. **Permanent delete (guard):** try to permanent-delete an active pipeline → frontend disables button; backend returns 409 if called directly
6. **Filter chip persistence:** select "Only archived" → refresh → still on "Only archived"
7. **Backend /permanent route:** active pipeline → 409; archived pipeline → 204; nonexistent id → 404

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors

## What "done" looks like

1. Archive flow works with confirmation; pipeline disappears from default list.
2. Restore works without confirmation; pipeline returns.
3. Permanent delete works only on archived pipelines, requires name-type confirmation.
4. Filter chip works + persists.
5. Tests pass.
6. `check.sh` passes within exempt set.

## Report Codex submits

1. `git diff --stat` output.
2. Screenshots: kebab menu open, archive confirmation dialog, permanent delete with name-type field, filter chip in 3 states.
3. Test pass count including the new /permanent route tests.
4. Branch.

---

**Lead notes (not for Codex):**
- Two-step archive-then-permanent-delete is the right safety pattern for the "delete pipeline" UX. Users who want to delete have to first archive (low-friction), then permanent-delete (with name-type confirmation). Prevents accidental data loss.
- After this lands, pipelines have a clean lifecycle: active → paused → archived → permanently deleted, all surfaced in UI.
