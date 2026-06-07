# Memory Phase 4 — Scope tree + Recently-added feed

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/memory-phase-4-scope-tree`
**Browser smoke owner:** Lead, post-merge — open Memory page, expand the scope tree, click into a project scope, verify list narrows; click "Recently added", verify it shows last-day observations across all scopes.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~290 (extended scope endpoint + frontend tree component + tests).
**Priority:** MEDIUM — fourth phase. Ships after Phases 2, 1, 3.
**Parent plan:** `briefs/memory-ui-redesign.md`
**Companion audit:** `audits/memory-ux-audit.md`
**Depends on:** Phase 2, 1, 3 merged. Frontend reuses the row template Phase 1 built and the action panel Phase 3 built.

---

## Why this exists

Per the audit, the scope dropdown today is a flat list with raw composite strings like `agent · marketing.scout.regional_news (12d / 8o)`. Scopes form a hierarchy (Workspace → Projects/Agents/Brands/Global → specific IDs); the page treats them as a flat select. Jon needs a sense of *where in the system* memory lives.

Phase 4 replaces the dropdown with a tree component and adds a "Recently added" pseudo-scope at the top — the "what just landed" feed. Also surfaces multi-scope membership (when an observation belongs to >1 scope via `memory_observation_scopes`).

---

## Scope

### Part A — Backend: extend `/scopes` + `/observations`

Edit `artemis/memory/repository.py`:

**1. Extend `list_scopes`** (line 312) to return hierarchical data:

```python
[
  {
    "scope_kind": "workspace",
    "scope_id": "amira",
    "drawer_count": 12,
    "observation_count": 89,
    "children": [
      {"scope_kind": "project", "scope_id": "marketing-os", "drawer_count": 4, "observation_count": 22, "children": [...]},
      ...
    ]
  },
  ...
]
```

Source of truth: `memory_scopes` table has `parent_scope_kind` + `parent_scope_id`. Build the tree from those FKs. Scopes with no parent become roots. Counts aggregate up: each parent shows the sum of its own observations + all descendants.

Order children deterministically by `scope_id` ascending.

If a scope appears in `memory_observations.scope_kind`/`scope_id` but not in `memory_scopes`, treat as a synthetic root with no children (current behavior is a flat list, so this preserves existing scope visibility).

**2. Extend `list_observations`** (line 203) to support `include_descendants`:

New optional parameter:
- `include_descendants: bool = False`

When `True` + `scope_kind` + `scope_id` are all set: the query expands to include observations whose `scope_kind`/`scope_id` matches the target OR any descendant scope. Compute the descendant set with a recursive CTE on `memory_scopes`. If the target scope has no rows in `memory_scopes`, fall back to the literal-match filter.

**3. Recently added pseudo-scope:**

Not a new endpoint — just a frontend convention. "Recently added" = `?recency_from=<today_start>&sort=recent` against `list_observations` with no scope filter. Backend doesn't need to know about the pseudo-scope.

### Part B — Frontend: tree component

Edit `public/js/features/memory-shell.js`.

**1. Remove the scope dropdown** from `renderM6Shell` toolbar.

**2. Add a left rail** to the main layout (`m6-shell-layout` becomes 3-column: tree | list | detail).

Tree structure:

```
📅 Recently added
🌐 All scopes (N)

▼ workspace (M)
  • amira (M)

▼ project (P)
  ▶ marketing-os (8)
  ▶ memory-keystone (12)

▼ agent (Q)
  ▶ marketing.scout.regional_news (5)
  ▶ marketing.scout.daily_brief (3)
  ...

▼ brand (R)
  • amira (R)

▼ global (S)
  • root (S)
```

Each leaf and each parent is clickable. Click a parent → list filters to that scope WITH descendants. Click a leaf → list filters to that exact scope.

Expand/collapse state persists in `localStorage` under `artemis-memory-scope-tree-expanded`.

Counts shown next to each node.

**3. Multi-scope row chip:**

In `renderM6ListPanel` (Phase 1 template), if an observation has multiple scope entries in `memory_observation_scopes`, add a small `+N scopes` chip next to the primary scope chip. Detail panel (Phase 2 template) lists all scopes with weight.

Note: the `memory_observation_scopes` table isn't currently surfaced through `list_observations`. Extend that endpoint to project a `secondary_scopes: list[{scope_kind, scope_id, weight}]` field via a single JOIN. If empty, return `[]`.

### Part C — Tests

`artemis/memory/tests/test_scope_tree.py` (new file):

1. **`list_scopes` returns hierarchical structure.** Fixture: 1 workspace, 2 projects under it, 3 agents under one project. Verify nesting + counts.
2. **Counts aggregate correctly up the tree.** Fixture: leaf with 5 obs; parent with 2 obs; root sees 7.
3. **`list_observations?include_descendants=true` expands scope.** Fixture: project with 2 descendant agents. Filter on project + include_descendants returns union.
4. **`include_descendants=false` (default) limits to literal scope.** Same fixture; default returns only project-direct rows.
5. **Synthetic root for missing memory_scopes row.** Fixture: observation in scope (`x`, `y`) but no row in `memory_scopes`. Verify it appears as a top-level node in `list_scopes`.
6. **Multi-scope row carries secondary scopes.** Fixture: observation with primary scope (`agent`, `scout`) + 2 secondary scopes. Verify `secondary_scopes` returns both.

---

## Files owned

- EDIT: `artemis/memory/repository.py` (extend `list_scopes` for hierarchy; extend `list_observations` for `include_descendants` + `secondary_scopes`)
- EDIT: `artemis/routes/memory.py` (pass new query params through)
- EDIT: `public/js/features/memory-shell.js` (replace dropdown with tree component; add multi-scope chip; "Recently added" pseudo-scope)
- EDIT: `public/css/panels/memory.css` (tree-rail styles, expand/collapse caret, scope-count badge)
- NEW: `artemis/memory/tests/test_scope_tree.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/memory/tests/test_scope_tree.py -v` — all tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - Open Memory page; verify left-rail tree renders with workspace at the top.
   - Click "Recently added"; verify list shows today's observations across all scopes, sorted recent.
   - Expand a project scope; click a child agent; verify list narrows to that agent only.
   - Click the project scope itself; verify list shows union of project-direct + descendant agents.
   - Find an observation with multiple scopes (or seed one) and verify the `+N scopes` chip appears on the row; click into detail; verify all scopes listed.
   - **Paste a screenshot of the tree with at least one branch expanded.**
5. `git diff --stat` + `git log --oneline -1` on `worker/memory-phase-4-scope-tree`. **Paste.**

---

## Hard constraints

- **No schema changes.** Reads `memory_scopes` parent FKs as they are.
- **Read-only.** Phase 4 has zero write paths. The tree is a navigation surface only.
- **Tree depth bounded.** Don't render deeper than 3 levels by default; collapse anything below. Spec is "workspace → kind → leaf"; a 5-level tree would suggest a bug in scope_kind taxonomy upstream.
- **Expand state is non-blocking.** A user with all branches collapsed sees the same scope counts as a user with everything expanded. State is purely UI.
- **`include_descendants` is opt-in.** Default = false, preserves existing endpoint contract. New callers (the tree) pass it explicitly.
- **Performance ceiling.** Recursive CTE for descendants is fine at current scale (low hundreds of obs); if scope count grows beyond ~50, revisit with a materialized closure table — but not in this phase.
- **No new visual languages.** Tree component uses existing CSS tokens. Expand caret is a unicode "▶/▼" or existing chevron asset, not a new icon.
- **Local-only git.** Worker commits on `worker/memory-phase-4-scope-tree`; terminal-Lead merges after Lead approves.
