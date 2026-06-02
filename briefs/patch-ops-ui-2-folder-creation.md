# Patch — OPS-UI-2 Folder Creation UI (gap from OPS-UI-2)

**Owner:** Codex (paste-ready, mechanical UI)
**Branch:** `codex/patch-ops-ui-2-folder-creation`
**LOC budget:** ~120 (estimate; honest overrun OK up to ~180)
**Brief author:** Lead (Opus 4.7)
**Depends on:** OPS-UI-2 merged (`agents.metadata.display_folder` JSONB column exists, view toggle works, drag persists).

## Why this brief exists

OPS-UI-2 shipped the data layer for custom agent folders — `metadata.display_folder` round-trips correctly, view toggle works, drag-to-existing-folder persists. **But the UI provides no way to actually CREATE a folder.** All 18 agents currently sit under "Unsorted" with no creation affordance visible. The feature is dead-end without entry points.

OPS-UI-2's original brief specified TWO creation paths:
1. **Right-click a folder header** → context menu with "Create subfolder…" / "Rename folder…" / "Delete folder"
2. **"Add to folder…" inline affordance** on each agent row in slug view, with a "New folder…" option in the dropdown

Neither shipped. This patch adds both.

## Scope

### In scope

1. **Right-click context menu on folder headers** (in custom view):
   - On right-click of a folder header (including the "Unsorted" pseudo-folder if it's a real folder header in DOM): show a small context menu near the cursor
   - Menu items:
     - **Create subfolder…** → opens a prompt input → user types name → on submit, no immediate change (folder appears once first agent is dragged in OR fold below — see #3)
     - **Rename folder…** → opens inline edit on the folder name → on submit, PATCH every agent in that folder with new path
     - **Delete folder…** → confirm dialog → PATCH every agent in that folder to clear `display_folder` (move to Unsorted)
   - For "Unsorted" pseudo-folder: only "Create subfolder…" applies (can't rename/delete the sentinel)

2. **"Add to folder…" affordance on agent rows** (visible in both slug AND custom view):
   - On hover of an agent row, show a small "Move" or "Folder" icon button on the right side
   - Click → opens a dropdown:
     - List of existing custom folders (parsed from union of all agents' `display_folder` values)
     - Separator
     - **"New folder…"** option at the bottom → opens prompt input → submit → PATCHes the agent's metadata with the new path
   - On any folder selection → PATCH agent's `metadata.display_folder` → toast "Moved to {folder}"
   - In slug view: same dropdown but also flips view-mode to `custom` after the move (so the user sees their change land)
   - In custom view: just refreshes the tree in place

3. **"+ New folder" button at top of custom view tree** (third entry point — UX safety net):
   - Visible only in custom view, near the View toggle or above the folder list
   - Click → prompt input for folder name → submit → creates a placeholder empty folder
   - **Empty folders need to render** in the tree even with zero agents, until the user populates them. Add an `_empty_folders` localStorage list keyed by view session — when a folder is created via prompt but has no agents yet, it appears in the tree with an "Drop agent here" empty state.
   - Once a real agent is dragged in, the folder graduates from `_empty_folders` to natural (parsed from agent metadata). On reload, empty folders that still have zero agents disappear (cleaned from localStorage).

4. **Save confirmation toast on Agent Card edits** (bonus fix, surfaced in walkthrough):
   - Currently: clicking Save on the provider/model picker (or any agent detail-panel edit) persists correctly but provides no visual confirmation. Users don't know the save succeeded.
   - Fix: after any successful PATCH from the Agent Card detail panel (provider/model, persona, instructions, etc.), call the existing branded `showToast()` helper (the one Codex 1 added in `patch-wave-walkthrough-bugs`) with title "Saved" and a subtle subtitle like "{Agent name} updated."
   - Find every PATCH callsite in the agent detail-panel save handlers and add the toast call after `.then(...)` resolves.
   - On error: toast with title "Save failed" + error message subtitle, using the same toast system but with warning color treatment.
   - **Scope:** Agent Card panel only. Don't touch Pipeline canvas save (PIPE2 polish brief covers that) or Skill / other panels (out of scope).

4. **Tests:**
   - Right-click on folder header → context menu appears
   - Click "Create subfolder" → prompt input → submit "Test" → folder appears (via empty-folders localStorage)
   - Drag an agent into "Test" → PATCH fires; agent.metadata.display_folder = "Test"; folder graduates
   - Click "Add to folder" on agent row → dropdown shows existing folders + "New folder…"
   - Click "New folder…" → prompt → submit → PATCH fires
   - Rename folder → all agents in folder updated
   - Delete folder → agents move to Unsorted, folder disappears

### Out of scope

- Nested folder DnD (drag folder INTO another folder). Folders are flat OR slash-pathed via prompt input ("Marketing/Priority"). One level of nesting via path naming only.
- Color/icon per folder. Text labels only.
- Bulk move (multi-select agents). Single agent at a time.
- Search within folder dropdown. Folder list is short enough; defer if it ever grows.
- Folder reordering (drag folder headers to reorder). Alphabetical only.

## Invariants

1. **agent_id NEVER changes** in any folder operation. Already enforced by OPS-UI-2's data model; reaffirmed here.
2. **No new backend routes.** Use existing PATCH `/api/agents/{id}` which already accepts `metadata.display_folder` updates per OPS-UI-2.
3. **Empty-folder localStorage** is a UI-only concept. Backend never knows about empty folders — they exist only in the user's localStorage until populated.
4. **The "Unsorted" pseudo-folder is non-deletable.** Sentinel for agents with `display_folder = null`.
5. **All three entry points use the same PATCH path.** No new API surface.

## Files expected

| File | LOC |
|---|---|
| `public/js/components/agent-tree.js` | ~60 delta (context menu logic, empty-folder rendering) |
| `public/js/features/operations-shell.js` (or agents.js) | ~40 delta (context menu wiring, "+ New folder" button, "Add to folder" affordance on rows, save toast on agent PATCH callsites) |
| `public/css/features/operations.css` (or agents.css) | ~30 delta (context menu styling, hover-affordance, empty-folder dropzone) |
| `tests/unit/frontend/test_folder_creation.js` (new or appended) | ~30 |

**Total: ~160 LOC.** Cap at 200 to accommodate the bonus save-toast fix.

## Test plan

1. **Right-click on folder header in custom view** → context menu visible.
2. **Create folder via context menu** → prompt input → submit → folder appears in tree (empty-folder rendering).
3. **Drag agent into newly created folder** → PATCH fires → agent moves → folder shows agent.
4. **Click "Add to folder" on agent row** → dropdown shows folders + "New folder…".
5. **Create folder via row dropdown** → PATCH fires → agent gets `display_folder` set → tree updates.
6. **Click "+ New folder" button at top of tree** → prompt → folder created (empty until populated).
7. **Rename folder** → all member agents updated with new path.
8. **Delete folder** → confirm → agents move to Unsorted, folder gone.
9. **Refresh page** → DB-persisted folders restored; empty localStorage folders disappear if still empty.

## Invariants Codex must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new JS console errors; existing OPS-UI-2 drag-to-existing-folder still works

## What "done" looks like

1. All three folder-creation entry points functional (right-click, row affordance, top button).
2. Empty folders render via localStorage until populated by drag.
3. Rename / Delete folder works end-to-end.
4. agent_id never changes.
5. Tests pass.
6. `check.sh` passes within exempt set.

## Report Codex submits

1. `git diff --stat` output.
2. Screenshots: context menu open, "+ New folder" button visible, row "Add to folder" dropdown open, newly created empty folder showing dropzone empty state.
3. Test pass count.
4. Branch.

---

**Lead notes (not for Codex):**
- This closes a real functional gap in OPS-UI-2. Without folder creation, the custom view is functionally inert.
- Three entry points is intentional: power users right-click, casual users use the row affordance, new users find the "+ New folder" button. Discoverability matters because custom-folder is a non-obvious feature.
- After this patch, Jon should be able to create "Top Picks" / "Marketing Priority" / whatever folders he wants without leaving the Agents page.
