# OPS-UI-2 — Agent Custom Folders (cosmetic view layer)

**Owner:** Codex (paste-ready) OR Sonnet Worker
**Branch:** `codex/ops-ui-2-agent-custom-folders` (or `worker/ops-ui-2-agent-custom-folders`)
**LOC budget:** ~300 (estimate; honest overrun OK up to ~380)
**Brief author:** Lead (Opus 4.7)
**Depends on:** OPS-UI-1 merged (agents tree view exists).
**Grounded in:** Jon's 2026-05-21 walkthrough feedback — "I would like the ability to add folder / reorganize agents (this should be cosmetic only and shouldn't affect pipelines)".

## Why this brief exists

OPS-UI-1 shipped the agents tree parsed from agent_id slugs. That's canonical structure. But Jon wants a **personal organization layer** on top: custom folders, drag-to-reorganize, all cosmetic — doesn't affect agent_id (which is what pipelines reference). Slug view stays for system understanding, debugging, and pipeline building; custom view is the daily-driver browsing surface.

## Scope

### In scope

1. **Backend — `agents.metadata.display_folder` JSONB field:**
   - PIPE1/M5/etc. already use `agents.metadata` JSONB for free-form data. OPS-UI-2 reads + writes a top-level key `display_folder` (string, e.g., `"My Top Picks"` or `"Marketing/Priority"`).
   - No new migration needed — `metadata` JSONB already supports arbitrary keys.
   - PATCH `/api/agents/{id}` already supports updating metadata. Verify the route accepts `metadata.display_folder` updates without rejecting them.

2. **View toggle in Agents page header:**
   - Toggle: `View: Slug | Custom`
   - State persists in localStorage key `artemis.agents.view-mode`
   - Default for new users (no localStorage value yet): `slug`
   - Once user moves an agent into a custom folder, automatically flip to `custom` view and remember that preference

3. **Slug view (current OPS-UI-1 behavior):**
   - Tree parsed from agent_id slugs (`domain.subdomain.name`)
   - Read-only structure — can't create/rename/move folders
   - "View: Slug" pill in header is highlighted when active

4. **Custom view (new):**
   - Tree parsed from each agent's `metadata.display_folder` value
   - Path format: `"FolderName"` (top-level) or `"FolderName/Subfolder"` (nested via /)
   - Agents with no `display_folder` value go into a synthetic `"Unsorted"` folder at the top
   - Folders are inferred from the union of all agents' `display_folder` paths — no separate folders table

5. **Custom view interactions:**
   - **Drag an agent row** onto a folder header → PATCH the agent with new `metadata.display_folder`
   - **Right-click a folder header** → context menu: "Create subfolder…" / "Rename folder…" / "Delete folder (move agents to Unsorted)"
   - **"Create subfolder"** → prompt for name → on submit, no agent change (folder is implicit; appears once you drag an agent into it)
   - Actually: simpler approach — when creating a folder, immediately prompt "Which agent to put in it?" OR just create an empty placeholder via a localStorage list of "empty folders" rendered in the tree. Latter is messier; former is cleaner. **Worker picks; default to former.**
   - **"Rename folder"** → PATCHes every agent in that folder with the new path
   - **"Delete folder"** → PATCHes every agent in that folder to clear `display_folder` (move to Unsorted)
   - **Drag a folder header onto another folder** → moves all child agents into that new path (path concatenation)
   - **Empty folders** (no agents) auto-disappear from the tree

6. **"Add to folder…" inline affordance** on each agent row in slug view:
   - Small icon button on hover → opens dropdown of existing custom folders + "New folder…" option
   - Clicking a folder name → PATCHes the agent's metadata.display_folder + offers a toast "Moved to {folder}. Switch to Custom view to see."
   - No drag in slug view (slug structure is canonical, don't let users drag agents OUT of it visually)

7. **Search + sort + filter** (existing OPS-UI-1 controls) work in both views. Filter chips ("Status: Healthy" etc.) apply identically. Search matches name+description+agent_id in both views.

8. **Tests:**
   - Default new user lands in slug view.
   - Drag agent into custom folder → metadata persists, view flips to custom, agent appears in folder.
   - Rename folder → all child agents updated.
   - Delete folder → agents move to Unsorted.
   - Toggle slug ↔ custom → both views render correctly with same data.
   - Search works in both views.

### Out of scope

- Sharing folder structures across users. Single-operator system; metadata is per-agent so it's already "shared" in the only sense that matters.
- Color/icon per folder. Just text labels for v1.
- Folder collapse state — already handled by OPS-UI-1's localStorage. Custom folders get their own localStorage keys.
- Reorganizing agents in slug view by dragging. Slug view is canonical, read-only.
- A separate folders table. Metadata JSONB is sufficient.
- Bulk move (multi-select agents → move all). Single agent at a time for v1.
- Drag-to-reorder agents within a folder. Sort dropdown handles ordering.

## Invariants

1. **agent_id never changes.** Custom folders are pure UI metadata — they DO NOT touch agent_id. Pipelines reference agents by agent_id; this stays stable.
2. **Slug view never modified.** It's the canonical lens. Cannot be edited from the UI.
3. **Custom view metadata persists to DB.** Not localStorage. So organization survives across devices / sessions / browsers — but the view-mode TOGGLE persists in localStorage (per-user preference).
4. **No new table.** Use existing `agents.metadata` JSONB.
5. **Existing OPS-UI-1 functionality unchanged** in slug view.

## Files expected

| File | LOC |
|---|---|
| `public/js/components/agent-tree.js` | ~120 delta (add custom-view branch + drag handler) |
| `public/js/features/operations-shell.js` (or wherever Agents page lives) | ~80 delta (view toggle, folder context menu, "Add to folder" hover affordance) |
| `public/css/features/operations.css` (or agents.css) | ~50 delta (folder dropzones, view toggle styling, context menu) |
| `artemis/routes/builders/agents.py` | ~10 delta (confirm metadata.display_folder update path works; minor adjustments if needed) |
| `tests/unit/frontend/test_agent_custom_folders.js` (new) | ~80 |

**Total: ~340 LOC.** Some over the 300 budget; cap at 380.

## Test plan

1. New user (no localStorage) → lands in slug view.
2. Drag Smoke Test Agent into a new "Favorites" folder → PATCH fires; agent's metadata.display_folder = "Favorites"; view flips to custom; agent appears in Favorites folder; Unsorted folder no longer contains it.
3. Create a folder "Marketing Priority" via context menu → folder appears; if no agent in it yet, render until first drag.
4. Drag agent into Marketing Priority → metadata updates; agent appears.
5. Rename "Favorites" → "Top Picks" → all member agents update; folder renders with new name.
6. Delete "Top Picks" → member agents move to Unsorted; folder disappears.
7. Toggle to slug view → tree renders by slug; custom folders not shown.
8. Toggle back to custom → custom folders restored.
9. Refresh page → view-mode persists per localStorage; folder structure persists per DB.

## Invariants Codex/Worker must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new JS console errors

## What "done" looks like

1. View toggle works.
2. Drag-to-folder works in custom view.
3. Folder create/rename/delete works.
4. agent_id unchanged regardless of folder operations.
5. Tests pass.
6. `check.sh` passes within exempt set.

## Report Codex/Worker submits

1. `git diff --stat` output.
2. Screenshot or description of both views.
3. Confirm agent_id unchanged after folder ops (paste a before/after DB query).
4. Test pass count.
5. Branch.

---

**Lead notes (not for Codex/Worker):**
- This is pure UX organization. The architecture stays clean because folder is a metadata field on the agent row, not a separate concept.
- Slug view stays default-for-new-users so the canonical taxonomy is the first thing new operators see. Custom view is opt-in.
- If implementation surfaces that PATCH of metadata.display_folder isn't currently supported on the agents route (validator strips unknown keys, etc.), add the explicit support and document.
