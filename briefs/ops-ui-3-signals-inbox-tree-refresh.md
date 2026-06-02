# OPS-UI-3 — Signals Inbox Tree Refresh

**Owner:** Codex (paste-ready) OR Sonnet Worker
**Branch:** `codex/ops-ui-3-signals-inbox-tree-refresh` (or `worker/ops-ui-3-signals-inbox-tree-refresh`)
**LOC budget:** ~700 (estimate; honest overrun OK up to ~900)
**Brief author:** Lead (Opus 4.7)
**Depends on:** M1 reason code registry, M3a state machine, M4 qualifier rules. Signals Inbox page already exists with current vertical-scroll list view.
**Grounded in:** Jon's 2026-05-21 walkthrough — "signals inbox is a vertical death scroll." Same problem OPS-UI-1 solved for Agents.

## Why this brief exists

Once scouts start running (M5b in flight; live signals when adapters move past stubs), Signals Inbox fills with rows fast. The current vertical-scroll list doesn't scale. OPS-UI-3 applies the same tree treatment OPS-UI-1 did for Agents: grouping, compact rows, search, filter chips, detail panel.

The signals data shape is richer than agents (reason codes, urgency tiers, geography, status lifecycle), so the grouping modes are configurable — user picks how to group, not a fixed structure.

## Scope

### In scope

1. **Grouping modes selector** at top of the inbox:
   - Pill toggle: `Group by: State | Reason Code | Geography | Urgency | Flat`
   - Default: `State` (qualified / pending_qualification / suppressed_stale / rejected_hard_filter / approved / etc.)
   - State value persists in localStorage `artemis.signals.group-by`

2. **Tree rendering per grouping mode:**

   - **By State:** top-level folders are SignalState values (per M3a's enum). Counts shown in parens. `qualified` and `approved` folders default-expanded; suppressed/rejected default-collapsed.
   - **By Reason Code:** top-level folders are reason_code values (POLICY_LIT_MANDATE, FUNDING_LITERACY_GRANT, etc. — from M1's seeded 17). Counts shown.
   - **By Geography:** top-level folders are state abbreviations (FL, IN, TX, etc.). Sub-folders are district names. Useful for territory-focused review.
   - **By Urgency:** top-level folders are `hot`, `standard`, `enrichment`. Urgent stuff at top.
   - **Flat:** no grouping — single scrollable list (the existing behavior, kept as escape hatch for power users).

3. **Compact row** (~60px — slightly taller than agent rows because signals have more inline info):
   - Left: small geography badge (state code, 2 chars, colored or muted) + district initial or icon
   - Center: brief signal title — typically `{reason_code} · {district_name}` (e.g., `POLICY_LIT_MANDATE · Pinellas County`)
   - Right column: urgency badge (`hot` red, `standard` muted, `enrichment` gray) + days-since-discovered timestamp ("12h ago", "3d ago")
   - Status dot far right: dot color = signal lifecycle state
   - Click row → loads signal detail panel on right

4. **Detail panel** (right side, replaces or augments existing — survey current implementation):
   - Header: signal_id (short, truncated for display), `state`, `urgency`
   - **Source section:** verbatim_snippet (1-3 sentences exactly as scout extracted), source URL (clickable), source type, speaker_attribution
   - **Reason codes section:** chip row of all reason codes assigned to this signal with their confidence values
   - **Qualifier audit:** if the signal has been through the qualifier, show which rules fired (from M4's qualifier_rule_applications audit table). Useful for "why was this suppressed?" debugging.
   - **Brief preview:** if a brief was composed for this signal, link to it (uses M7 writing studio overview).
   - **Action buttons:** Approve / Reject / Snooze / Archive — wired to existing approvals or to M3a transition() if signals approve via direct state transition.

5. **Filter chips above the tree:**
   - `Urgency: hot` (toggleable; selecting auto-filters tree)
   - `Urgency: standard`
   - `Urgency: enrichment`
   - `State: pending qualification` / `qualified` / `approved` / etc. (multi-select)
   - `Reason code:` searchable picker — open to select one or more
   - `Geography:` searchable picker — state-level
   - Active chips render in accent color; click again to deselect
   - Multiple chips combine as AND across categories, OR within a category (same pattern as OPS-UI-1)

6. **Search box** at top:
   - Type-ahead across signal_id + verbatim_snippet + district_name + reason_code values
   - Real-time tree filter; matching subtrees expand, non-matching collapse
   - ESC clears

7. **Sort within groups:**
   - Default: newest first (by discovered_at DESC)
   - Alt: urgency (hot first), then newest
   - Sort dropdown applies within each folder

8. **Empty state per folder:** if a folder has zero matching signals, render "No signals matching filter." Don't auto-hide the folder (jumpy UI).

9. **Empty state for whole page:** if no signals exist at all yet (M5b stub adapters have not yet produced live data) render: "No signals yet. Scouts run on the marketing pipeline's schedule. Trigger a manual run from Operations → Pipelines → Marketing Pipeline → Run." with a deep-link to the Pipelines page.

10. **Performance note:** signals will accumulate — projected 100s within first weeks, 10000s over months. Tree render with collapse keeps DOM manageable. If profiling shows lag past 1000 visible rows, add windowing/virtualization to the row renderer (out of scope for v1 — flag if it surfaces).

11. **Tests:**
    - Each grouping mode renders correctly with mock data.
    - Search filters.
    - Filter chips compound correctly.
    - Sort reorders within group.
    - Detail panel loads on row click.
    - Empty states render.
    - localStorage persistence of grouping + collapse states.

### Out of scope

- Bulk actions (multi-select signals → bulk approve/reject). Single signal at a time for v1.
- Editing the qualifier rules from this page. Rules live in the qualifier surface; deep-link out.
- Re-running the qualifier on a specific signal. Buttons stay on the signal flow, not here.
- New filter dimensions beyond what's listed (e.g., by district enrollment, by score). Add later if asked.
- Backend changes — `/api/signal-queue` already returns everything needed. Survey first to confirm.

## Invariants

1. **No backend changes** unless the existing `/api/signal-queue` endpoint is missing data. If it is, add the fields rather than restructuring the response (additive only).
2. **No new design tokens.** Use existing palette.
3. **Detail panel reuses existing pattern** if one already renders signal detail elsewhere. Don't reinvent.
4. **Performance smoke** with 200 mock signals must remain responsive.

## Files expected (honest estimate)

| File | LOC |
|---|---|
| `public/js/features/signals-inbox.js` (existing or new) | ~250 delta (tree mount, group switcher, search/sort/filter wiring) |
| `public/js/components/signal-tree.js` (new) | ~200 |
| `public/js/components/signal-detail-panel.js` (new or refactor) | ~150 |
| `public/css/features/signals.css` (existing or new) | ~150 |
| `tests/unit/frontend/test_signals_inbox_tree.js` (new) | ~80 |

**Total: ~830 LOC.** At the cap (700) limit; honest overrun OK to 900. If you're heading past 900, ping Lead with structural reason.

## Test plan

1. **Group by State** default → tree renders with state folders.
2. **Change grouping** to Reason Code → tree re-renders with reason code folders. localStorage updates.
3. **Search "Pinellas"** → only Pinellas-related signals visible; tree expands to show them.
4. **Filter chip Urgency: hot** → only hot signals visible.
5. **Combine chips** Urgency: hot + State: qualified → AND filter applied.
6. **Click signal row** → detail panel loads with verbatim snippet, reason codes, qualifier audit.
7. **Sort by urgency** within group → hot rows appear at top.
8. **Empty data state** → friendly empty message + deep-link to Pipelines page.
9. **Performance** — 200 mock signals + filter typing stays responsive.

## Invariants Codex/Worker must NOT regress

- conftest hard-fail on non-test DB
- dotenv `override=False`
- No `git push`
- `pwd && git branch --show-current` before state-changing Bash
- `git diff --stat` for LOC self-reporting
- `./scripts/check.sh` passes within exempt set
- `git switch lead/j6a-granola-integration` after commit
- Browser smoke: no new console errors

## What "done" looks like

1. Signals Inbox renders as tree.
2. Grouping mode switcher works (5 modes).
3. Search, sort, filter all functional.
4. Detail panel populates from existing /api/signal-queue.
5. Compact rows.
6. Empty states friendly.
7. Performance smoke clean with 200 mock signals.
8. Tests pass.
9. `check.sh` passes within exempt set.

## Report Codex/Worker submits

1. `git diff --stat` output.
2. Screenshots: each grouping mode + filtered + detail panel.
3. localStorage keys used.
4. Performance note (200 mock signals).
5. Test pass count.
6. Branch.

---

**Lead notes (not for Codex/Worker):**
- After OPS-UI-3 lands, the Signals Inbox is ready to handle real scout output. Once M5b's scout adapters produce live signals, this is the surface Josh/Angela use to triage. Volume could ramp fast — design accordingly.
- The qualifier-audit section in the detail panel uses M4's audit data — it's the "why was this signal suppressed/boosted?" explanation. This is real value for tuning the qualifier rules over time.
- If the existing signal detail panel surface is good and just needs the inbox list-side reworked, the detail panel component can stay mostly as-is. Survey before assuming a rewrite.
