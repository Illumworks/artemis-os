# Dev Projects v2 — Addendum: folder picker (backend-driven, not browser-native)

**Why this exists:** Codex's v2 work hit a wall trying to use `window.showDirectoryPicker()`. The browser API returns a `FileSystemDirectoryHandle` for security-sandboxed file ops; it deliberately does NOT expose the absolute filesystem path. The backend's `DevProjectCreate` schema requires `path: str` — a real filesystem path. These don't bridge in the browser. Custom in-app folder browser backed by the server is the only viable path for a local-first Mac app.

This is the same pattern Claude Code's startup picker uses and the same one Codex CLI uses internally. You know it well.

## Backend — add one read-only filesystem endpoint

Add to `artemis/routes/dev_projects.py`:

```python
@router.get("/browse")
async def browse_directories(
    path: str = Query(default="~"),
) -> dict[str, Any]:
    """List subdirectories of `path` for the project-folder picker.
    
    Returns:
      - resolved_path: the path after ~ expansion + realpath
      - parent_path:   parent dir, or None if at root
      - entries:       sorted list of subdirectories (dirs only, no files)
                       each entry: {name, path, is_git_repo}
    
    Hidden directories (starting with `.`) excluded unless `path` itself
    is hidden. Symlinks resolved. Errors return 400 with a structured
    {"error": "...", "code": "..."} envelope.
    """
    from pathlib import Path
    
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise HTTPException(
            status_code=400,
            detail={"error": f"Path does not exist: {path}", "code": "path_not_found"},
        )
    if not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail={"error": f"Not a directory: {resolved}", "code": "not_a_directory"},
        )
    
    entries = []
    try:
        for entry in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") and not resolved.name.startswith("."):
                continue
            entries.append({
                "name": entry.name,
                "path": str(entry),
                "is_git_repo": (entry / ".git").exists(),
            })
    except PermissionError:
        # Show what we can; just return empty if the whole dir is locked
        pass
    
    parent = str(resolved.parent) if resolved.parent != resolved else None
    return {
        "resolved_path": str(resolved),
        "parent_path": parent,
        "entries": entries,
    }
```

**Security notes:**
- This is a local-only app on a single-user Mac mini. We're not exposing this through the tunnel to the internet — only `localhost` access matters here. If the tunnel is ever opened to multiple users, this endpoint becomes a directory-listing oracle and needs auth + a root-jail. Document this as a follow-up.
- `Path.resolve(strict=True)` prevents traversal-style abuse (`../../../etc/passwd` resolves to the real path; if it doesn't exist, raises).
- Hidden directory handling matches macOS Finder's default (hide `.foo`, show contents of `.foo` if user navigated into it explicitly).

## Frontend — custom folder browser modal

Replace the `window.prompt()` flow in `public/js/features/dev_projects.js` with a modal that uses the new endpoint.

### Modal structure

```
┌──────────────────────────────────────────────────┐
│ Add a project                                  × │
├──────────────────────────────────────────────────┤
│ /Users/artemis/Desktop/Artemis        [Up]       │  ← breadcrumb-style current path + Up button
│                                                  │
│ 📁 artemis-os                  (git)             │  ← clickable rows; (git) tag on .git-bearing dirs
│ 📁 claudeck-artemis            (git)             │
│ 📁 Other Project                                 │
│ 📁 ...                                           │
│                                                  │
├──────────────────────────────────────────────────┤
│ Project name: [artemis-os                    ]   │  ← auto-fills from current dir's basename
│                                                  │
│           [ Cancel ]   [ Use this folder ]       │
└──────────────────────────────────────────────────┘
```

### Behavior

- [ ] On modal open, default current path to user's home (`~`). Backend resolves to `/Users/artemis`. Show its subdirectories.
- [ ] Clicking a `📁 row` calls `GET /browse?path=<that path>` → re-renders the list inside the modal. Breadcrumb at top updates.
- [ ] Clicking "Up" goes to `parent_path` from the latest response. Disabled when `parent_path === null`.
- [ ] Project name field auto-fills from the current path's basename whenever the path changes. User can edit.
- [ ] "Use this folder" button is enabled when the current resolved path is a directory (always true if the modal is showing entries). On click: POST `/api/dev-projects/projects` with `{name, path: <current resolved path>}`.
- [ ] If backend returns 400 with `code: "path_not_found"` on the initial `~` resolution (very unlikely on a Mac), fall back to a manual text input. Don't crash.
- [ ] Show `(git)` tag in muted text next to entries that have a `.git` subdirectory — helps Jon spot the project folders quickly amid sibling dirs.
- [ ] Keyboard: arrow-up/down to navigate the list, Enter to descend into a folder, Cmd+Enter to "Use this folder," Esc to close.
- [ ] Loading state: while the `/browse` request is in flight, show a small spinner; don't blank the list.
- [ ] Error state: if `/browse` returns 400 (permission denied on a specific dir, or path went missing between renders), show the error inline above the list and keep the previous list visible.

### Polish

- Hover state on rows
- Click outside the modal closes it (with confirm if there's a non-empty project name typed)
- Modal traps focus when open (standard accessibility)
- Width: comfortable for paths up to ~80 chars without horizontal scroll

## Acceptance — what done looks like

- [ ] `window.prompt()` is **gone** from `public/js/features/dev_projects.js`
- [ ] Clicking "+ new project" opens the folder browser modal
- [ ] Starting at `~`, user can navigate down into `Desktop/Artemis/artemis-os` in three clicks
- [ ] User can click "Up" to navigate back, and the breadcrumb / list updates correctly
- [ ] Selecting `artemis-os` and clicking "Use this folder" creates the project and closes the modal
- [ ] Project name field auto-fills "artemis-os" but is editable before submit
- [ ] `(git)` tag visible next to `artemis-os`, `claudeck-artemis`, and any other git-bearing directories
- [ ] Keyboard nav works (arrows, Enter, Cmd+Enter, Esc)
- [ ] Permission-denied directories don't crash the browser — they show empty content or an inline error
- [ ] The new endpoint has a route test for the happy path + the 400 path_not_found case
- [ ] `ruff check` + `mypy` clean on the backend addition

## Quality gates

- [ ] Screenshot of the modal in three states pasted in your report: initial `~` view, descended-into-Artemis view, project-name-edited view
- [ ] `git diff --staged` before commits — twice-bitten pattern; CONVENTIONS.md "CWD trap" section is required pre-read
- [ ] If you touch the existing `dev_projects.js` modal scaffolding, keep the rest of the v2 sidebar work intact

## Where to start

1. Read this addendum + the main `dev-projects-v2-codex.md` brief if you haven't already
2. Implement the backend `/browse` endpoint first — testable in isolation via curl
3. Then the frontend modal, replacing the `window.prompt()` call
4. Verify all five acceptance bullets above with manual smoke before reporting done
