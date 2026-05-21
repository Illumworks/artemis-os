# OPS-UI-1 — Agents Page Tree Refresh

**Owner:** Codex (paste-ready) OR Sonnet Worker — mechanical UI work with existing tokens
**Branch:** `codex/ops-ui-1-agents-tree-refresh` (or `worker/ops-ui-1-agents-tree-refresh`)
**LOC budget:** ~700 (full-diff insertions; cap at 850)
**STOP CONDITION:** if you reach 700 insertions, STOP and ping Lead.
**Brief author:** Lead (Opus 4.7)
**Depends on:** M5 marketing agents seeded (18+ agents in DB to render). No backend changes.
**Grounded in:** Jon's 2026-05-21 feedback — "vertical scroll is impressive and this is just the beginning; need a more compact way of displaying agent card and sortable sort of like the folder structure of the dev projects."

## Why this brief exists

The Agents page today renders all 18 agents as ~150px vertical cards. With 18 agents the scroll is unwieldy; with the projected 50–100 agents (post-onboarding of CSM agents, sales agents, etc.) it becomes unusable. The good news: agent slugs follow `domain.subdomain.name` convention (e.g., `marketing.scout.starbridge_researcher`), which is a natural folder tree by string split.

OPS-UI-1 replaces the vertical-scroll list with a **collapsible folder tree** (left), **compact rows** (~50px each), **search + sort + filter**, while keeping the detail panel (right) intact. After this brief: the Agents page scales to hundreds of agents without breaking the UX.

## Scope

### In scope

1. **Slug parsing utility** in `public/js/features/agents.js` (or wherever Agents lives — survey):
   - Given a list of agent rows, group by parsing `agent_id` on `.`:
     - Level 1 = domain (e.g., `marketing`, `operations`, `personal`)
     - Level 2 = subdomain (e.g., `scout`, `qualifier`, `content`, `none` if only 2 segments)
     - Level 3 = the agent leaf
   - Agents without dots in their `agent_id` (legacy like `smoke-test` or `ws-integration`) fall under a synthetic `Personal` or `Uncategorized` folder. Detect and route them.
   - Function: `buildAgentTree(agents) -> { [domain]: { [subdomain]: Agent[] } }`.

2. **Tree component** `public/js/components/agent-tree.js` (new):
   - Renders the tree from `buildAgentTree` output.
   - Domain headers (`Marketing`, `Operations`, `Personal`) are large, collapsible. Click toggles expand/collapse. State persists to `localStorage` keyed by domain name.
   - Subdomain headers (`Scout (9)`, `Qualifier (4)`, `Content (3)`) are medium, collapsible. State persists per-subdomain.
   - Leaf rows are compact (~50px): avatar (initial in circle), name, model (small text on right), status dot (green = healthy, amber = warning, gray = never run), last-run timestamp on the right.
   - Click a leaf row → fires existing `selectAgent(agentId)` handler. Detail panel pattern unchanged.
   - Active selection has a left accent bar (existing token).

3. **Search + sort + filter row** above the tree:
   - **Search box** (type-ahead): filters across `name` + `agent_id` + `description`. As user types, tree filters in real-time; matching subtree expands; non-matching subtrees collapse. ESC clears.
   - **Sort dropdown** within folders: `By name (A-Z)` (default), `By last run (newest first)`, `By run health`. Affects leaf order within each subdomain only — folders stay in their alphabetical order.
   - **Filter chips** (toggle on click, multiple selectable):
     - `Status: Healthy` (only show healthy)
     - `Status: Needs attention` (only show amber/red)
     - `Status: Never run` (only show last_run == null)
     - `Trigger: Manual` (only show agents in no scheduled pipeline)
     - `Trigger: Scheduled` (only show agents in scheduled pipelines)
   - Filter chips combine as AND between categories, OR within a category. Active chips render in accent color.

4. **Empty state per folder:** if a folder has zero agents after filter, render a tiny "No agents matching filter." Don't hide the folder entirely (jumpy UI).

5. **Header card stays:** "WHO DOES WORK / Agents / A roster for scanning…" — keep as-is. Update the badge counts (ROSTER, RUN HEALTH, SKILLS LINKED, MEMORY) to reflect the full agent set, not the visible-after-filter set.

6. **Detail panel stays:** the right-side AGENT PROFILE / PERSONA / SOUL / IDENTITY / PURPOSE / RECENT RUNS pattern doesn't change. Only the left list view is reworked.

7. **CSS** — `public/css/features/agents.css` (or wherever Agents styles live):
   - Use existing tokens (`--text`, `--muted`, `--surface-1`, `--surface-2`, `--accent`, `--warning`, `--success`, etc.).
   - Compact row height: 48–52px. Tight but not cramped.
   - Folder header: 36px, subdomain header: 32px (or whatever the existing app's heading scale uses for nested headings).
   - Hover state on rows: subtle background lift (existing pattern from other lists).
   - Active row: left accent bar + slightly elevated background.
   - Status dot: 8px circle, color via tokens.

8. **Tests:**
   - Frontend integration test: render the page with mock data of 18 agents across 3 domains; verify tree renders with correct grouping.
   - Search: type "starb" → only Marketing > Scout > Starbridge Researcher visible.
   - Sort by last_run: leaves reorder within subdomain.
   - Filter chip "Status: Never run" → only agents with `last_run = null` visible.
   - Click a leaf → `selectAgent()` called with correct agent_id.
   - localStorage persistence: collapse a folder, reload page, folder stays collapsed.

### Out of scope

- Pagination. With folder collapse + filter, scaling to 500+ agents in one rendered DOM is fine for v1.
- Bulk actions (multi-select, batch enable/disable). Single-select detail view only.
- Drag-to-reorder. Folder structure is parsed from slugs, not user-rearrangeable.
- Renaming agent_ids to change folder. That's an agent-rename concern; orthogonal.
- New backend endpoints. Existing `/api/agents` returns everything needed.
- The "Build with Agent-Builder" / "New agent" buttons at the top — leave as-is.
- Agent Card detail panel improvements — orthogonal, that's a separate brief if needed.

## Invariants

1. **Light DOM, no Shadow DOM.** Per CLAUDE.md convention.
2. **Existing design tokens only.** No new hex colors, no new spacing constants.
3. **Detail panel binding unchanged.** The right panel reads from the same `selectedAgentId` state slot it currently does. This brief only changes the left list view.
4. **localStorage persistence keyed by stable namespace** (e.g., `artemis.agents.tree.collapsed`). Don't conflict with existing keys.
5. **Performance:** rendering 100 agents must be smooth (no perceptible lag on filter/sort). Use virtualization only if profiling shows lag; otherwise plain DOM is fine.

## Files expected

| File | LOC |
|---|---|
| `public/js/features/agents.js` (existing) | ~150 delta (replace list render with tree mount; wire search/sort/filter) |
| `public/js/components/agent-tree.js` (new) | ~250 |
| `public/js/components/agent-tree-search.js` (new — optional, can inline) | ~80 if separate; ~30 if inlined |
| `public/css/features/agents.css` | ~150 delta (tree + compact row + filter chips) |
| `tests/unit/frontend/agent-tree.test.js` (new) | ~80 |

**Total: ~700 LOC.** Compact rows + chips + persistence is more than it looks. Cap at 850 if you find you need a touch more in CSS.

## Test plan

1. **Tree renders.** 18 agents in 3 domains, 6 subdomains; verify all leaves present.
2. **Compact row.** Each leaf is 50px or less.
3. **Folder collapse.** Click domain header → all subdomains hidden. Click subdomain header → that group's leaves hidden.
4. **Persistence.** Collapse a folder, refresh page, folder still collapsed.
5. **Search.** Type "qualifier" → only Marketing > Qualifier subtree visible.
6. **Sort.** Sort by last_run. Leaves with `last_run = null` go last. Leaves with timestamps order by newest first.
7. **Filter chip.** "Status: Never run" → only never-run agents show.
8. **Click leaf.** `selectAgent` fires; detail panel updates.
9. **Empty state.** Filter to a query with no matches → friendly empty state, page doesn't break.
10. **Performance smoke.** Render 100 mock agents; filter input typing doesn't lag.

## Invariants Codex/Worker must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set before declaring done
- Browser smoke: no new JS console errors; existing Agent Card detail panel still works
- `git switch lead/j6a-granola-integration` after commit

## What "done" looks like

1. Agents page shows a tree, not a vertical scroll list.
2. Compact rows, ~50px each.
3. Search + sort + filter all functional.
4. Folder collapse persists.
5. Detail panel still loads on row click.
6. CSS uses existing tokens only.
7. Tests pass.
8. `check.sh` passes within exempt set.
9. Full-diff insertions ≤ 850.

## Report Codex/Worker submits

1. `git diff --stat` output.
2. Screenshot or description of the new tree view (empty state, filtered, expanded).
3. The localStorage key namespace used (paste).
4. Test pass count.
5. Branch.
6. Any visual judgment that surfaces in the brief that wasn't pre-specified — flag with a one-line note for Lead to review post-merge.

---

**Lead notes (not for Codex/Worker):**
- This is the first scalable-list pattern for Artemis. If it lands well, the same tree approach generalizes to Skills (which will also explode in count), Pipelines (eventually), and any future first-class list.
- The detail panel deliberately stays untouched — this brief is scoped tight to the left list view. Future briefs can polish the detail panel separately (e.g., trim the persona editor when not in edit mode to reclaim vertical space).
- If Codex hits a judgment fork (e.g., "search should match `tools` array too, or just name+description?"), default to name+description+agent_id. Tools matching is power-user; defer.
