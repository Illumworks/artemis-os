# Memory Phase 1 — Make rows speak

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/memory-phase-1-rows-speak`
**Browser smoke owner:** Lead, post-merge — open Memory page, verify rows show category badge + score + recency, filter chips narrow the list, search debounces and filters.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~300 (extended list endpoint + frontend filter UI + tests).
**Priority:** HIGH — second phase of the Memory page redesign. Ships after Phase 2 (provenance) lands.
**Parent plan:** `briefs/memory-ui-redesign.md`
**Companion audit:** `audits/memory-ux-audit.md`
**Depends on:** Phase 2 must be merged first (Phase 1 reuses the same `m6-list-row` template that Phase 2 leaves intact). Independent backend.

---

## Why this exists

Per the audit, every row in the Memory list looks identical today. The backend knows `category`, `score`, `hit_count`, `confidence`, `evidence_count`, and `accessed_at` for every observation — none of it is rendered. Jon described the surface as "one long list of coded memories that are not clickable or surface anything." Phase 2 fixes the *detail panel* narrative; Phase 1 fixes the *list panel* scannability.

After Phase 1, a glance at the list tells you: which memories are conventions vs warnings, which have high confidence vs which are stale, which were added today, and which match a search.

---

## Scope

### Part A — Extend list endpoint

Edit `artemis/memory/repository.py`, `list_observations` (line 203). Add three optional query parameters and project four new fields into each row.

**New optional parameters:**
- `category: str | None` — filter to one of `warning`, `discovery`, `decision`, `convention`.
- `recency_from: datetime | None` — only return observations with `created_at >= recency_from`.
- `status: str` — one of `active` (default — `valid_until IS NULL`), `superseded` (`superseded_by IS NOT NULL`), `all`.

**New row fields:**
- `category` (already on the model)
- `score` (already on the model)
- `evidence_count` (already on the model; surface the column value)
- `pinned_at` (NULL today; Phase 3 adds the column — for Phase 1 the field will always be NULL but the JSON shape stays stable)

Edit `artemis/routes/memory.py`, `list_observations_endpoint` (line 170). Add the three Query params, pass through.

### Part B — Frontend list row + filters

Edit `public/js/features/memory-shell.js`:

**1. Row template** (`renderM6ListPanel`, line 392). Each row now renders:
- Top line: category badge (color-coded chip) + scope chip + age label (relative)
- Middle: existing content preview
- Bottom meta: score gauge (small 0–100 bar) + evidence-count chip + (if pinned) pin icon

Category color tokens (define in CSS):
- `warning` → red (`--memory-cat-warning`, ~rgba(230, 84, 73, ...))
- `discovery` → amber (`--memory-cat-discovery`)
- `decision` → blue (`--memory-cat-decision`)
- `convention` → green (`--memory-cat-convention`)

**2. Filter chip row** (between toolbar and list panel):

Three chip groups, horizontal, with counts:

- **Category:** All · Warning (N) · Discovery (N) · Decision (N) · Convention (N)
- **Recency:** All · Today · This week · This month
- **Status:** Active · Superseded · All

Filter state lives in `m6State.filters = { category, recency, status }`. Chip clicks update state and re-fetch the list.

Recency values translate to `recency_from`:
- Today → `new Date().setHours(0,0,0,0)` as ISO
- This week → 7 days ago as ISO
- This month → 30 days ago as ISO
- All → omit param

Counts on each chip come from a small `/api/memory/stats` extension (see Part C) so they're accurate vs the full database, not just the current page.

**3. Search box** (above filter chips):

A debounced search input (300ms). For Phase 1: client-side substring match on the current 50-row page's `content_preview`. Server-side full-text search is out of scope — explicitly noted in the brief.

Search and filters compose: search narrows what's already filtered.

**4. Sort dropdown** (rightmost in the filter row):

Three options: Recent (default — `created_at desc`), Most cited (`evidence_count desc, created_at desc`), Highest score (`score desc, created_at desc`).

Backend already orders by `created_at desc`. Extend `list_observations` to accept `sort: str` (one of `recent`, `cited`, `score`) and pick the order_by accordingly.

### Part C — Stats endpoint extension (small)

Edit `artemis/memory/repository.py`, `get_memory_stats` (line 352). Add three new fields to the return dict:

- `by_category` — `dict[str, int]` counts per `warning`/`discovery`/`decision`/`convention`
- `new_today` — count of observations with `created_at >= today_start`
- `needs_attention` — count of unresolved conflicts (UNION) observations with `category='warning' AND score >= 0.8 AND valid_until IS NULL`

These feed the filter chip counts and the hero "pulse" numbers (the hero update is part of this phase since it depends on these new fields).

Edit hero render in `renderM6Shell` (line 323). Replace the current four chips with:

> **Memory**
> 247 memories · 12 new today · 3 need attention · 6 scopes

(Header label stays "Memory" per locked decision #5. The pulse line below carries the voice.)

### Part D — Tests

`artemis/memory/tests/test_list_observations_filters.py` (new file):

1. **Filter by `category` narrows result.** Fixture: 4 obs across 4 categories. `?category=warning` returns 1 row.
2. **Filter by `recency_from` narrows by date.** Fixture: 5 obs with `created_at` spread across 60 days. `?recency_from=<7d ago>` returns last-week subset.
3. **`status=active` excludes superseded.** Fixture: 3 active + 2 superseded. `?status=active` returns 3.
4. **`status=superseded` returns only superseded.** Same fixture, `?status=superseded` returns 2.
5. **`sort=cited` orders by evidence_count desc.** Fixture: obs with evidence counts [1, 3, 2]. Verify order.
6. **`sort=score` orders by score desc.** Fixture: scores [0.3, 0.9, 0.6]. Verify order.

`artemis/routes/tests/test_memory_shell_routes.py` (extend):

7. **`get_memory_stats` returns `by_category`, `new_today`, `needs_attention`.** Fixture: known counts. Verify the new fields are populated.

---

## Files owned

- EDIT: `artemis/memory/repository.py` (extend `list_observations`, extend `get_memory_stats`)
- EDIT: `artemis/routes/memory.py` (pass through new query params)
- EDIT: `public/js/features/memory-shell.js` (row template, filter chips, search, sort, hero pulse line)
- EDIT: `public/css/panels/memory.css` (category-badge variants, score gauge, filter chip row, search input style)
- NEW: `artemis/memory/tests/test_list_observations_filters.py`
- EDIT: `artemis/routes/tests/test_memory_shell_routes.py` (test #7)

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` is unchanged. **Paste.**
2. `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/memory/tests/test_list_observations_filters.py artemis/routes/tests/test_memory_shell_routes.py -v` — all tests pass. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Manual smoke (Lead does this post-merge):**
   - Open Memory page; verify rows show category badges in their correct colors.
   - Click "Warning" chip; verify list narrows to warning-category rows only and count updates.
   - Type a word from a known observation into search; verify list filters in <500ms.
   - Switch sort to "Most cited"; verify order changes.
   - Verify hero reads "N memories · M new today · K need attention · J scopes" with non-zero correctness against the DB.
   - **Paste a screenshot of the populated list with filter chips visible.**
5. `git diff --stat` + `git log --oneline -1` on `worker/memory-phase-1-rows-speak`. **Paste.**

---

## Hard constraints

- **Read-only.** Phase 1 has zero write paths. The `pinned_at` field is read-only here — Phase 3 adds the column + write endpoint.
- **No mid-flight pagination break.** When filters change, reset offset to 0. When sort changes, reset offset to 0. Don't fetch page 2 with stale filter state.
- **Counts must come from `/stats`, not from the current page.** Filter chip counts read from `by_category`; if the chip says "Warning (5)" the database has 5 warnings, not "5 in the current 50-row page."
- **No new visual languages.** Category badges reuse existing chip primitives. Score gauge is a small horizontal bar component sized to match existing UI.
- **Search is client-side only in Phase 1.** Do NOT add an `?q=` server param — it's tempting but is its own work. Frame search as "filter the current page" and the limitation gets handled gracefully (when the page slides past 50 rows, search results may be partial — show a small "search the next page" affordance OR just document the limitation in row count text).
- **Empty-state messaging when filters return zero.** "No memories match the current filters" — not the existing "memory is still populating" copy (which only fires when the DB is truly empty).
- **Local-only git.** Worker commits on `worker/memory-phase-1-rows-speak`; terminal-Lead merges after Lead approves.
- **Order discipline.** Do NOT block on Phase 2 if Lead chooses to run them in parallel later. They touch overlapping CSS classes; coordinate via Lead.
