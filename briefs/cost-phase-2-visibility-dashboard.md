# Cost Phase 2 — Visibility dashboard (profile menu + page + Spend tab)

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-phase-2-visibility-dashboard`
**Browser smoke owner:** Lead, post-merge — click avatar → profile popover → click "Cost" → verify the page loads with this-month spend, all breakdowns, daily chart, and top-calls table.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~500 (rollup endpoint + profile menu item + shell route + page).
**Priority:** HIGH — first visible delivery of the cost page.
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`
**Depends on:** Phase 1 merged (this phase reads from `cost_events`).

---

## Why this exists

Phase 1 lands the data foundation. Phase 2 makes it visible. After this phase, Jon can open the Cost page from his profile menu and immediately answer "how much did I spend this month, where did it go, and what's the trend."

The existing JS at `public/js/features/cost-dashboard.js` (156 LOC) is dormant — it calls an endpoint that doesn't exist. Phase 2 replaces it entirely with a new Artemis-flavored implementation; it's small enough that no porting is warranted.

---

## Scope

### Part A — Unified rollup endpoint

NEW: `GET /api/costs/summary`

Query params (all optional, with defaults):
- `from`: ISO8601 datetime (default: start of current month UTC)
- `to`: ISO8601 datetime (default: now)
- `feature_tag`: comma-separated filter
- `provider`: comma-separated filter
- `model`: comma-separated filter

Response shape:

```json
{
  "window": {"from": "2026-06-01T00:00:00Z", "to": "2026-06-06T14:30:00Z"},
  "prior_window": {"from": "2026-05-01T00:00:00Z", "to": "2026-05-06T14:30:00Z"},
  "totals": {
    "cost_usd": 87.40,
    "input_tokens": 12345678,
    "output_tokens": 4567890,
    "cache_creation_tokens": 123456,
    "cache_read_tokens": 987654,
    "cache_savings_usd": 18.40,
    "calls": 234
  },
  "prior_totals": {
    "cost_usd": 134.20,
    "calls": 412
  },
  "today": {
    "cost_usd": 4.20,
    "avg_daily_cost_usd": 6.00
  },
  "by_feature": [
    {"feature_tag": "agent_run", "cost_usd": 42.00, "share": 0.48, "calls": 89, "input_tokens": 5400000, "output_tokens": 2100000},
    ...
  ],
  "by_model": [
    {"provider": "anthropic", "model": "claude-sonnet-4-6", "cost_usd": 58.00, "share": 0.66, "calls": 142},
    ...
  ],
  "by_provider_path": [
    {"provider_path": "api", "cost_usd": 71.00, "share": 0.81},
    {"provider_path": "cli", "cost_usd": 16.40, "share": 0.19}
  ],
  "daily": [
    {"date": "2026-06-01", "cost_usd": 5.20},
    {"date": "2026-06-02", "cost_usd": 7.40},
    ...
  ],
  "top_calls": [
    {"id": 12345, "created_at": "2026-06-06T12:00:00Z", "feature_tag": "trajectory_summary", "provider": "anthropic", "model": "claude-sonnet-4-6", "input_tokens": 1234567, "output_tokens": 234567, "cost_usd": 5.40},
    ...
  ]
}
```

`prior_window` is the same calendar duration ending one period before. `share` is fraction of `totals.cost_usd`. `cache_savings_usd` is computed as `(cache_read_input_tokens * (input_rate - cache_read_rate)) / 1_000_000` summed across rows.

`top_calls` returns top 20 by `cost_usd`, descending.

All queries are single GROUP BY on `cost_events` with the time-range filter and optional facet filters. With the indexes from Phase 1, this is fast at expected scale (10K-100K rows).

Auth: `Depends(require_token)` like other dashboards.

### Part B — Profile menu wiring

Edit `public/js/ui/artemis-shell.js`, function `initProfilePopover` (line 541). Insert a new item between Connectors and the divider:

```html
<div class="settings-pop-item" data-action="cost">
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
    <line x1="12" y1="1" x2="12" y2="23"/>
    <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
  </svg>
  <span>Cost</span>
</div>
```

Handler in `handleAction`:

```javascript
case 'cost': {
  setState("view", "cost");
  break;
}
```

### Part C — Shell route + page

Add `cost` as a recognized view in `public/js/core/navigation.js` (or wherever views are normalized — Worker greps to find it, mirroring `MEMORY_VIEW`).

Add `loadCostShell` entry point that mounts on view change.

NEW: `public/js/features/cost-shell.js` (the page).

Structure:

```javascript
// cost-shell.js
let costState = {
  window: 'this-month',         // 'today' | 'this-week' | 'this-month' | 'last-30-days' | 'custom'
  customFrom: null,
  customTo: null,
  providerFilter: null,
  modelFilter: null,
  data: null,                    // last fetched summary
  loading: false,
  tab: 'spend',                  // 'spend' | 'routing' | 'cloud' | 'budgets' — phase 2 only renders 'spend'
};

export async function loadCostShell() { /* fetch + render */ }
function renderCostShell() { /* hero + tabs + tab body */ }
function renderSpendTab(data) { /* cards + breakdowns + chart + table */ }
```

**Hero block:**

> **Cost**
> **Projected API cost this month: $87.40** · last month: $134.20 (down 35%) · actual subscription: flat
> [horizontal sparkline of daily spend MTD, normalized]

> **UX guard — MANDATORY framing.** The hero number is *synthetic API cost* — what these calls would cost at on-demand API rates. Jon is on flat subscriptions; the headline must never be mistakable for "a bill I am paying." Lead the dollar amount with the word **Projected** (or equivalent — "Projected API cost", "What this would cost on API", "API-equivalent"). Always pair it inline with a short reminder that the actual subscription is flat. This applies to the hero block AND to the "This month" card in the card grid below. Subhead/tooltip elaboration must explain: "Subscription is flat; this number shows what the same volume of calls would cost at on-demand API rates — useful for cloud-deployment projections and routing decisions." Tone is plain, not jargon; do not introduce a new visual treatment, just use existing tokens. Worker MUST land this framing or the smoke fails.

**Tabs row:** `[Spend] [Routing opportunities] [Cloud infra] [Budgets]`

Phase 2 only renders Spend tab content. The other tabs render placeholder cards:

```
┌─────────────────────────────────────────┐
│  Routing opportunities                   │
│  Coming in a follow-up phase.            │
│  This tab will show where you could save │
│  by routing some features to Gemini or   │
│  OpenAI.                                 │
└─────────────────────────────────────────┘
```

(Same shape for Cloud infra + Budgets — clearly marked as future, not broken.)

**Spend tab content:**

1. **Toolbar:** Time window dropdown · Provider filter · Model filter · Custom date range picker (only visible when window=`custom`)
2. **Card grid (4 cards):**
   - **Projected API cost this month**: `$X` · vs last month: `$Y` (Δ %) · subtext: "Subscription is flat — this is on-demand API equivalent."
   - Today (projected API): `$X` · vs avg daily: `$Y` (Δ %)
   - Tokens: `Xm in / Ym out`
   - Cache savings: `$X` (this period)
3. **Two-column breakdown:**
   - Left: "Spend by source bucket" — vertical list, bar visualizes `share`. Each row clickable → filter the toolbar.
   - Right: "Spend by model" — same structure.
4. **Daily chart** — horizontal bar list, last 30 days, normalized to max. (Per the Node prototype pattern.)
5. **Top calls table** — sortable: Feature · Model · Tokens · Cost · When. Sortable client-side.

CSS lives in NEW `public/css/panels/cost.css`. Reuse existing tokens.

### Part D — Tests

`artemis/routes/tests/test_costs_summary.py` (new):

1. **Summary aggregates correctly.** Seed 10 cost_events across 3 feature_tags / 2 models. Hit `/api/costs/summary?from=…&to=…`. Verify `totals.cost_usd` = SUM, `by_feature` groups correctly, `by_model` groups correctly.
2. **Prior window aligns to same duration.** Seed events in current month + prior month. Verify `prior_window` is correct duration ending one window before.
3. **Cache savings computed correctly.** Seed events with cache_read_input_tokens > 0. Verify `cache_savings_usd` math.
4. **Filter by feature_tag narrows.** `?feature_tag=agent_run` returns only those.
5. **Filter by provider narrows.** `?provider=anthropic` returns only those.
6. **`today` block uses today UTC start.** Seed events spanning today + yesterday. Verify `today.cost_usd` is just today.
7. **`top_calls` returns top 20 by cost desc.** Seed 30 events with varying costs. Verify length=20, ordered correctly.

No frontend test infra; Lead does eyes-on smoke (acceptance #4 below).

---

## Files owned

- NEW: `artemis/routes/costs.py`
- EDIT: `artemis/main.py` (register the new router)
- EDIT: `public/js/ui/artemis-shell.js` (add Cost item to profile popover)
- EDIT: `public/js/core/navigation.js` (add COST_VIEW constant; normalize)
- NEW: `public/js/features/cost-shell.js`
- NEW: `public/css/panels/cost.css`
- EDIT: `public/js/features/home.js` (mount cost-shell on view change, mirroring the Memory pattern)
- NEW: `artemis/routes/tests/test_costs_summary.py`

---

## Acceptance criteria

1. **No schema changes.** `uv run alembic current` unchanged (Phase 1 already added cost_events). **Paste.**
2. **Backend tests pass.** `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/routes/tests/test_costs_summary.py -v`. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt. **Paste.**
4. **Live smoke (Lead does post-merge):**
   - Click avatar in left rail → profile popover opens → verify "Cost" item is between Connectors and the divider.
   - Click "Cost" → page loads.
   - Verify hero shows "This month: $X · last month: $Y" with non-zero values (assumes Phase 1's backfill seeded history).
   - Verify all four cards render with non-zero numbers.
   - Verify "Spend by source bucket" has at least 3 buckets visible.
   - Verify "Spend by model" has at least 2 models visible.
   - Verify daily chart renders with at least one non-zero bar.
   - Verify top-calls table shows up to 20 rows with sortable columns.
   - Switch the time window dropdown to "Today" and "Last 30 days"; verify the data refetches and updates.
   - Verify Routing/Cloud/Budgets tabs each render a "coming soon" placeholder, NOT a broken state.
   - **Paste a screenshot of the populated Spend tab.**
5. `git diff --stat` + `git log --oneline -1` on `worker/cost-phase-2-visibility-dashboard`. **Paste.**

---

## Hard constraints

- **Read-only.** Phase 2 doesn't mutate `cost_events`. The page is a viewer.
- **Reuse existing visual primitives.** Cards, tables, chart — use existing tokens. New CSS file is for layout, not new design language.
- **No paging on top_calls in this phase.** Top 20 is enough. Pagination can land in a later micro-phase if Jon wants the full call log.
- **Time-window dropdown options:** Today · This week · This month · Last 30 days · Custom. Custom shows two date inputs.
- **Comparison logic must be consistent.** Hero "vs last month" compares this-month-to-date with last-month-same-date-range; not "all of last month."
- **No analytics events fired from the cost page** (don't add tracking here; keep it sterile).
- **Placeholder tabs are honest.** "Coming in a follow-up phase" with one-line description, not "Loading…" forever.
- **Local-only git.** Worker on `worker/cost-phase-2-visibility-dashboard`; Lead merges after smoke.
