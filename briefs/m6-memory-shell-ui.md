# M6 — Memory shell UI wiring + drawer/observation inspector

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/m6-memory-shell-ui`
**Browser smoke owner:** Lead, post-merge — open Operations → Memory shell, verify drawers + observations render with scope filter + evidence chain inspector.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~250 (backend list/detail routes + frontend wiring + tests).
**Priority:** MEDIUM-HIGH — third member of Round 1 memory keystone P4 stream. Independent of M1 and M5. Once M1 + M5 produce real data, M6 makes it visible to humans.

---

## Why this exists

Per `docs/memory-audit-2026-05-29.md` finding #F + `docs/hollowness-audit-2026-05-29.md` 🟠 dormant surface listing:

> `public/js/features/memory-shell.js` (lines 35-148) defines three "rooms" (review queue, project memory, working memory, agent memory). Mounts in Operations shell. **No backend endpoints wired; loading state is permanent. No drawer/observation inspector UI found in public/js.**

The memory shell is one of the UI surfaces the previous Opus session built as scaffolding without wiring. It mounts, renders a loading state, and never resolves. M6 wires it to real backend routes and adds the drawer/observation inspector that surfaces the evidence chain.

After M6: operators can see what memory holds, click into any observation to see the evidence (drawers + agent_runs + signal_queue rows) that produced it, filter by scope (agent / workspace / brand / global). The keystone plan's "wings/rooms frontend" Slice 4 ships.

---

## Scope

### Part A — Backend list/detail routes

Extend `artemis/routes/memory.py` (the existing module per the memory audit's import-graph finding) with:

**`GET /api/memory/drawers`** — paginated list:
- Query params: `scope_kind` (optional, filter), `scope_id` (optional, filter), `limit` (default 50, max 200), `offset` (default 0)
- Returns: `{"drawers": [{id, scope_kind, scope_id, content_preview (first 200 chars), source, created_at}], "total": <int>, "offset": <int>}`

**`GET /api/memory/observations`** — paginated list with same query shape:
- Returns: `{"observations": [{id, scope_kind, scope_id, content_preview, superseded_by, created_at}], "total": <int>, "offset": <int>}`

**`GET /api/memory/observations/{id}`** — detail with evidence chain:
- Returns: `{"observation": <full row>, "evidence": [{source_kind, source_id, weight, source_preview, ...}]}`
- Evidence rows include source preview where possible (e.g. for `memory_drawer` sources, include drawer content excerpt; for `agent_run` sources, include the trajectory summary's `what_worked`; for `signal_queue` sources, include the headline)

**`GET /api/memory/scopes`** — list of distinct scopes with row counts:
- Returns: `[{"scope_kind": "agent", "scope_id": "marketing.scout.regional_news", "drawer_count": 12, "observation_count": 8}, ...]`
- Used by the scope-filter UI

**`GET /api/memory/stats`** — overall counts for the dashboard:
- Returns: `{"total_drawers": N, "total_observations": N, "total_evidence_links": N, "scope_count": N, "by_scope_kind": {...}}`

All routes require `Depends(require_token)` per the existing pattern in `artemis/routes/memory.py`. All read-only.

### Part B — Frontend wiring

Replace the stub state in `public/js/features/memory-shell.js`. Add:

1. **Header bar** with overall stats from `/api/memory/stats` (e.g. "247 drawers · 89 observations · 412 evidence links · 6 scope kinds")
2. **Scope filter** — dropdown or chip-row populated from `/api/memory/scopes`. Filter the listing.
3. **Two-column layout:**
   - Left: paginated list of observations (default tab) or drawers (toggle). Each row shows content_preview + scope + created_at.
   - Right: detail pane — when an observation is clicked, fetch `/api/memory/observations/{id}` and render the evidence chain:
     - Parent observation content
     - "Backed by:" list of evidence sources with their previews
     - If the observation is superseded, show the supersedor with a link
4. **Empty state** — clear messaging when memory is sparse (still 1 row today; will be many after M1 + M5 land): "Memory is still populating. New observations will appear here as agents run and signals qualify."
5. **No write surface in M6.** This brief is READ-ONLY. Writing to memory happens at the producer surfaces (M1, M3, M5). M6 visualizes; it does not edit.

CSS lives in `public/css/features/memory-shell.css` (or wherever the existing styling lives — match the existing pattern). Use existing design tokens.

### Part C — Tests

`artemis/routes/tests/test_memory_shell_routes.py`:

1. **`GET /api/memory/drawers` returns paginated shape.** Fixture: 10 drawers across 2 scopes. Verify default limit, offset, total.
2. **Filter by scope_kind narrows result.** Fixture: drawers in `agent` and `workspace`. Filter `?scope_kind=agent`. Verify only agent-scoped rows returned.
3. **`GET /api/memory/observations/{id}` returns evidence chain.** Fixture: observation with 2 evidence links (1 drawer, 1 agent_run). Verify response includes both with previews.
4. **`GET /api/memory/scopes` aggregates row counts correctly.** Fixture: 3 scopes with varying drawer/observation counts. Verify response matches.
5. **`GET /api/memory/stats` returns totals.** Fixture: known counts. Verify totals match.

`public/js/features/tests/test_memory_shell.spec.js` (or skip if no JS test harness — Lead does eyes-on smoke):

6. **Memory shell loads stats on mount.** Mock `/api/memory/stats`. Verify header renders.
7. **Clicking an observation row fetches detail.** Mock the listing + detail. Verify network call + detail pane updates.

---

## Files owned

- EDIT: `artemis/routes/memory.py` (add 5 new GET endpoints)
- EDIT: `artemis/memory/repository.py` (add list/detail query helpers if not present)
- EDIT: `public/js/features/memory-shell.js` (replace stub with real wiring)
- EDIT: `public/css/features/memory-shell.css` (or wherever) — match existing pattern
- NEW: `artemis/routes/tests/test_memory_shell_routes.py`
- POSSIBLE: `public/js/features/tests/test_memory_shell.spec.js` if JS test infra exists (skip otherwise; Lead does eyes-on)

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` shows `0047`. **Paste.**
2. `ARTEMIS_TEST_DB_URL=... uv run pytest artemis/routes/tests/test_memory_shell_routes.py -v` — all 5 backend tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - Open Operations → Memory shell
   - Verify header stats render (counts match `psql`-queried totals)
   - Click into the existing observation row (the single user-written one, until M1 + M5 land more)
   - Verify detail pane renders (evidence chain may be empty for the single existing observation)
   - Filter by scope_kind — verify listing narrows
   - **Paste a screenshot or DOM snippet showing the populated shell.**
5. `git diff --stat` + `git log --oneline -1` on `worker/m6-memory-shell-ui`. **Paste.**

---

## Hard constraints

- **Read-only.** M6 has zero write paths. No edit/delete buttons. No "approve" or "supersede" UI. Memory writes happen at producer surfaces only.
- **No new visual languages.** Use existing CSS tokens + component patterns. Match the look-and-feel of the Pipelines page, Agents page, etc.
- **Handle the empty state gracefully.** Today there's 1 observation. After M1 + M5 land, there will be more. The UI must not break on either extreme.
- **Pagination is required.** Don't load all rows on mount. Default limit 50.
- **No new schema.** Read existing tables only.
- **Local-only git.** Worker commits on `worker/m6-memory-shell-ui`; terminal-Lead merges after Lead approves.

---

## Visual reference

Loose layout (Worker has aesthetic latitude within design system):

```
┌─────────────────────────────────────────────────────────────┐
│  Memory                                                      │
│  247 drawers · 89 observations · 412 evidence links          │
│                                                              │
│  Scope: [ All ▾ ]   [Drawers] [Observations]                │
├──────────────────────┬──────────────────────────────────────┤
│  obs 89 · agent      │  Observation #89                      │
│  "Run 329 stalled..." │  Scope: agent · marketing.qualif…    │
│  Run 329 · 2h ago    │  Created: 2026-05-29 ...              │
│                      │                                       │
│  obs 88 · workspace  │  Backed by:                           │
│  "Signal 187 qualified│  • Drawer #142 (signal_queue:187)    │
│   under POLICY_..."  │    "headline: LAUSD screen-time..."    │
│  Signal 187 · 3h     │  • signal_queue:187                    │
│                      │    "qualified · POLICY_EDTECH..."     │
│  obs 87 · agent      │                                       │
│  ...                 │  Superseded by: (none)                │
└──────────────────────┴──────────────────────────────────────┘
```

Match the existing Operations grid aesthetic. Don't introduce new icons or palettes.

---

## Report-back format

```
M6 — Memory shell UI report
1. Commit / branch / worktree
2. LOC diff stats per file (backend + frontend split)
3. Tests added + pass count
4. Smoke screenshot or DOM snippet showing the populated shell
5. Stats-route response — PASTE the live response from /api/memory/stats
6. check.sh summary
7. Anything surprising — especially around existing memory-shell.js scaffolding interactions or pagination edge cases
```

---

**Worker: M6 is the third Round-1 brief in the memory keystone P4 stream (M1 trajectory write + M5 signal write + M6 UI). Independent of M1 and M5; can fire in parallel. After all three land + M1 + M5 produce data, operators see real memory for the first time. The shell stops being scaffolding and becomes a working surface — and humans can finally answer "what does the platform remember?" by clicking, not by querying the DB.**
