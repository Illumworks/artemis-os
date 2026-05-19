# Dev Projects v2 — left-rail rebuild to mirror Codex CLI's project + session pattern

**Owner:** Codex (you). The existing Dev Projects backend (`artemis/dev_projects/` + `artemis/routes/dev_projects.py`) was your earlier work and is solid. This brief rebuilds the **frontend** to match the UX you yourself ship in your CLI.
**Scope:** ~400-600 LOC frontend rewrite, ~50 LOC optional backend add. Half-day.
**Depends on:** Nothing. Runs in parallel with J10, J11.
**Blocks:** Nothing critical — Dev Projects is supplementary surface.
**Runs in parallel with:** J10 (trailing-slash) and J11 (Agents Operations parity), both of which are Worker-shaped on the backend. No file collisions expected.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why

The Dev Projects v1 implementation (commit `c7a4673`, your earlier shipment) wired the backend cleanly but the frontend has three blocking UX gaps:

1. **The "+ New project" button calls `window.prompt()` for the project folder path** — a browser-native popup, no validation, no file picker, no autocomplete. See `public/js/features/dev_projects.js:288-290`. The user has explicitly called this out as broken.
2. **The "Select a project" button doesn't open a picker** — it just renders an empty state because no projects exist yet. There's no UX flow to discover or pick one.
3. **The layout doesn't match how Claude Code and Codex actually work.** In your CLI's left rail, projects are folders and each folder expands to show its recent chat sessions with timestamps. Click a session → resume it. The user's exact words: *"i actually like the project folder and chat system how codex has in the left rail."*

You're being asked to rebuild Dev Projects so its left rail mirrors the pattern Codex CLI uses for projects + sessions. You know that pattern intimately — that's why this is going to you.

## Visual reference (from Codex CLI's own sidebar)

```
┌─────────────────────────────────────┐
│ Projects                  ↕  ▽  📁+ │   ← header with sort / filter / new-project
├─────────────────────────────────────┤
│ 📁 artemis-os                       │   ← project folder (collapsed shows just folder)
│   📌 Execute audit operations brief│   ← active/pinned session
│      Build Claude-style Codex UX 20h│   ← recent session + relative time
├─────────────────────────────────────┤
│ 📁 claudeck-artemis                 │
│      Plan marketing OKR dashboard 5d│
│      Fix Writing Studio formatting 1w│
│      Clean up Writing Agent memory 1w│
│      Add linked doc export flow   1w│
│      Add Writing Studio sync ctrls 1w│
│      Show more                       │
└─────────────────────────────────────┘
```

Key UX properties:
- Projects are collapsible folders
- Inside each folder, recent sessions list with title + relative time on the right
- One session per project can be "pinned" (📌) — sticky at top
- Active session is highlighted
- Hovering a project folder reveals a "+ new session" affordance
- Header has: collapse-all icon, filter/sort icon, new-project icon
- "Show more" link expands the recent-sessions list when truncated

## Backend — what already works

The backend you shipped supports everything you need. From `artemis/routes/dev_projects.py`:

```
GET    /api/dev-projects/projects                       → list projects
POST   /api/dev-projects/projects                       → create {name, path}
PATCH  /api/dev-projects/projects/{project_id}          → update
DELETE /api/dev-projects/projects/{project_id}          → archive
GET    /api/dev-projects/projects/{project_id}/sessions → list sessions in project
POST   /api/dev-projects/projects/{project_id}/sessions → create session in project
GET    /api/dev-projects/sessions/{session_id}          → session detail
PATCH  /api/dev-projects/sessions/{session_id}          → update (rename, pin?)
DELETE /api/dev-projects/sessions/{session_id}          → archive
GET    /api/dev-projects/sessions/{session_id}/messages → message history
POST   /api/dev-projects/sessions/{session_id}/messages → send message
... plus annotations, permissions, files routes
```

Schema (`artemis/dev_projects/schemas.py`):
- `DevProjectCreate` requires `name` + `path` (both non-empty strings)
- `DevProjectRead` returns `{id, name, path, ...}`
- Sessions have `id, project_id, title, updated_at` (verify the exact fields)

## Backend — optional small additions

These are nice-to-have but not required if you'd rather keep the slice frontend-only:

- [ ] **Path validation endpoint**: `POST /api/dev-projects/projects/validate-path` body `{path}` → returns `{ok: bool, exists: bool, is_dir: bool, error?: str}`. Calls `pathlib.Path(path).is_dir()`. Lets the "+ new project" modal show inline validation before submit. ~15 LOC.
- [ ] **Pin/unpin a session**: extend `PATCH /api/dev-projects/sessions/{id}` to accept `pinned: bool` and add a `pinned` column to the sessions table (alembic migration, next sequential revision). Optional — could also stash pinned state in localStorage on the frontend. Backend is more durable but more work.
- [ ] **Include `latest_session` + `recent_sessions[5]` in the projects list response** to avoid an N+1 fetch when rendering the left rail. ~20 LOC.

Your call on which (if any) of these to do. The frontend rebuild is the main event.

## Frontend — the main slice

Rewrite the left-rail rendering in `public/js/features/dev_projects.js` + components in `public/js/components/dev-projects-*.js` to match the visual reference above.

### A. Sidebar layout

- [ ] Header bar at top of the Dev Projects sidebar with three icon buttons:
  - **Collapse all** (↕) — collapses every project folder
  - **Filter / sort** (▽) — opens a small menu: sort by name / sort by most-recently-used / show archived
  - **New project** (📁+) — opens the new-project modal (see B below)
- [ ] List of project folders. Each folder row shows:
  - Folder icon + project name + caret (▸ when collapsed, ▾ when expanded)
  - Hover: reveals a "+ new session" icon button on the right (no fly-out menu — direct click)
  - Click on folder name: toggle expand/collapse
- [ ] When expanded, the folder shows up to 5 recent sessions, indented:
  - Session title (single line, truncated if long)
  - Relative time on the right (`20h`, `5d`, `1w` — match Codex CLI's formatting)
  - Pinned sessions (📌) appear at top of the list regardless of recency
  - Active session highlighted (different background)
- [ ] If there are more than 5 sessions, append a "Show more" link that fetches the full list (paginated 50 at a time).
- [ ] Click on a session row → switches the main panel to that session's chat view (call existing `loadSession(sessionId)` or equivalent).
- [ ] Right-click (or three-dot menu on hover) on a session row offers: Rename / Pin/Unpin / Delete. On a project folder: Rename / Open folder in file explorer / Remove from Artemis.

### B. New-project modal (replace `window.prompt()`)

- [ ] Click "+ new project" → opens a modal (use existing modal pattern in `public/js/components/` as reference)
- [ ] Modal contents:
  - Title: "Add a project"
  - **Project folder** label + path input + **Browse…** button to the right
    - "Browse…" calls `window.showDirectoryPicker()` (Chrome 86+, the user's target browser on this Mac mini). On success, populates the input with the picked directory's name + writes the full path. Catch the rejection (user cancelled) silently.
    - Fallback: if `showDirectoryPicker` isn't available (Safari, Firefox), hide the Browse button and let the user paste/type the path. Don't crash.
    - As the user types, debounce a call to `POST /validate-path` (if you added that endpoint in optional backend) and show ✓ or ⚠ with the error inline.
  - **Project name** label + input
    - Auto-fills from the last path segment when the path changes (`/Users/jon/code/my-app` → `my-app`)
    - Editable so the user can override
  - **Cancel** / **Create** buttons in the footer
- [ ] On Create: POST `/api/dev-projects/projects` with `{name, path}`. On success: close modal, refresh sidebar, auto-expand the new project, focus its "+ new session" affordance.
- [ ] On error: show the backend error message inline above the button row. Common errors: path doesn't exist, project with same path already exists.

### C. New-session inside a project

- [ ] Hovering a project folder reveals "+ new session" icon at the right. Click → POST `/api/dev-projects/projects/{id}/sessions` (no body needed, or `{title: "New session"}` — confirm via existing schema). On success: switches main panel to the new empty session, sidebar shows it at the top of the project's sessions list.

### D. Persistence + restoration

- [ ] On page load, the sidebar fetches projects + their recent sessions. The previously-active session (from `localStorage` or backend) reopens automatically.
- [ ] Collapsed/expanded state per project persists across reloads (localStorage). New projects default to expanded.
- [ ] Sidebar width is resizable (existing pattern in Artemis if there is one; otherwise skip).

### E. Empty states

- [ ] No projects yet: sidebar shows a centered "+ Add your first project" CTA. Click → opens the new-project modal. Drop the current "Select a project" button — it's confusing because there's nothing to select.
- [ ] Project has zero sessions: folder expanded shows "Start a new session" CTA in place of the sessions list. Click → creates a session.
- [ ] Sessions exist but are all archived: show "All sessions archived. Show archived?" toggle.

## Out of scope (separate briefs)

- Annotation rail (the right-side URL preview + notes from your v1 work) — stays as-is for now.
- Cross-project session search — separate brief.
- Drag-to-reorder projects — separate brief.
- Importing a Claude Code project's existing session history — separate brief.
- Multi-user / shared projects — single-user only.

## Acceptance — what done looks like

- [ ] Open the Dev Projects rail → see the rebuilt left sidebar matching the visual reference
- [ ] Click "+ new project" → modal opens with folder picker (or path input fallback on Safari/Firefox)
- [ ] Use the picker → directory chosen → project name auto-populates → click Create → new project appears in sidebar, auto-expanded, focused
- [ ] Click "+ new session" inside that project → new empty chat session opens in main panel
- [ ] Type a message, exchange a few turns, close the tab, reload the page → that session reopens automatically and history is preserved
- [ ] Create a second project; switch between them via the sidebar; each project's sessions list shows correctly
- [ ] Right-click a session → Rename works → the new title shows in the sidebar immediately
- [ ] Pin a session → it sticks to the top of its project's list
- [ ] Collapse a project folder → state persists across reload
- [ ] Test in Chrome (primary target) AND a fallback test in Safari (Browse button hidden, manual path input works)
- [ ] `window.prompt()` is **removed** from `public/js/features/dev_projects.js`

## Quality acceptance gates

- [ ] Manual smoke output pasted **verbatim** in your report (screenshots of: empty state, new-project modal, populated sidebar with two projects and a few sessions, session resume after reload, rename in action)
- [ ] No regression on the rest of Artemis — open Calendar, Meetings, Focus, Jira Board, OKR Studio after the Dev Projects rebuild and confirm they still render
- [ ] If you added the `/validate-path` endpoint: route test + manual curl in report
- [ ] If you added the pin column: alembic up/down round-trip
- [ ] **`git diff --staged` before every commit that includes file moves or renames** — `git mv` stages renames but follow-up `Edit` content changes don't auto-stage. Bit the project twice already (commits `bc13611`, `720e2c8`). Read `briefs/CONVENTIONS.md` "CWD trap" + commit-discipline sections before your first commit.
- [ ] `ruff check` + `mypy` clean if you touched backend
- [ ] No `TODO` / stub responses / placeholder text in shipped code

## Where to start

1. Read this brief twice
2. Run the app locally on `http://localhost:8000` and click into Dev Projects. Observe the current `prompt()` UX firsthand so you know exactly what you're replacing.
3. Read `public/js/features/dev_projects.js` and the six `public/js/components/dev-projects-*.js` components to map what exists. Most are 30-50 LOC each.
4. Read `artemis/routes/dev_projects.py` to confirm the backend contract — no surprises there, it's your work.
5. Decide whether to add the optional backend endpoints (validate-path is highly recommended; pin is nice-to-have; recent_sessions in list is a nice perf optimization).
6. Frontend rewrite. Start with the sidebar component, then the new-project modal, then session restoration logic.
7. Run the manual smoke in Chrome before reporting done.

## Coordination notes

- Two other Workers may be running in parallel: J10 (trailing-slash compatibility) and J11 (Agents Operations parity). Neither touches Dev Projects code; no merge friction expected.
- The Lead pair (Claude-context-Lead + terminal-Lead) is available for clarification questions but try the brief first — it's intentionally cold-readable.
- When you're done, post your report with the verbatim smoke output + the screenshots. Lead will do a 5-min audit before merging.

You shipped Dev Projects v1. v2 is finishing the job to match the UX users actually want — and the one you literally ship every day.
