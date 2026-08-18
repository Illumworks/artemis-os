# Build brief — Unify signals into one page (priorities on top, inbox below)

**Agent:** Codex (FE — `public/js/features/marketing-os.js` + `public/js/core/navigation.js` +
`public/index.html` nav rail; CSS as needed). **Branch:** `worker/signals-unify` off **current `main`**.
**Own git worktree, cd inside it, own test DB `artemis_test_signals`.** **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md`. Runs in PARALLEL with the composer pass — different files, no collision
(do NOT touch `composer-v5.js`). Jon decision (Creative Director): the two signal pages are confusing —
"which do I actually look at?" — so merge them into ONE page.

## Today (three signal nav items)
- **Signals Inbox** (`marketing-os.js` ~2932, `renderMarketingSignals` / signal tree) — the full firehose of
  every signal, grouped. Uses `listSignalQueueApi`.
- **Where to focus** (`marketing-os.js` ~4870, `loadMarketingPrioritization` / `renderMarketingPrioritization`)
  — a RANKED shortlist of the same signals (velocity + recency). Uses `fetchMarketingPrioritizationApi`.
- **Signal Playbook** (`marketing-os.js` ~4528) — NOT a "look at signals" page; it's the **config/registry**
  of signal criteria + reason codes read by scouts/qualifier.

## What to build — ONE "Signals" page
Merge **Where to focus** + **Signals Inbox** into a single view:
1. **Top section = the ranked shortlist** ("Where to focus" — what matters now). This is the daily starting
   point; show it first, above the fold. Reuse `renderMarketingPrioritization` + its controls/actions
   (`_wirePrioritizationActions`) as-is.
2. **Below = the full inbox**, inside a **collapsible "Show all signals" section** (collapsed-or-expanded
   default = your call; default to EXPANDED so nothing feels hidden, with a clear toggle to collapse). Reuse
   `renderMarketingSignals` + the signal tree + `_wireSignalActions` as-is. Do NOT reimplement either —
   compose the two existing renders into one container, each loading from its existing API.
3. **One nav item** named **"Signals"** (replace the two separate "Signals Inbox" + "Where to focus" entries
   in `navigation.js` + `index.html` rail). Keep deep-links/back-compat: old `#...signals-inbox` and
   `where-to-focus` routes should resolve to the unified page (redirect/alias in `navigation.js`) so nothing
   404s.
4. **Signal Playbook → out of the daily nav.** It's config: move it to a secondary/settings location (e.g.
   under a settings/admin area or a small "⚙ Playbook" link from the Signals page header), NOT a top-level
   daily rail item. Don't delete it — just relocate so the daily nav is the inbox + priorities only.

Lossless / additive: reuse both existing data sources and renders; no signal data changes; no backend schema
change (both APIs already exist).

## Design touchpoints (Creative Director — Lead + Jon eyeball before merge)
The page name ("Signals"), the collapsed/expanded default of the full inbox, and where Playbook relocates to
are look-and-feel calls. Build sensible defaults (above), screenshot, and Lead will review with Jon before
merge — expect a tweak pass.

## Acceptance (verify the EFFECT — browser, screenshots)
- ONE "Signals" nav item; clicking it shows the ranked shortlist on top and the full inbox below (toggle
  works). The old two items are gone from the rail.
- Old signals-inbox / where-to-focus deep links still land on the unified page (no 404).
- Signal Playbook is reachable but NOT in the primary daily nav.
- Both sections still load real data and their existing actions (snooze/reject/prioritization controls) still
  work — prove with a live load. No console errors. `./scripts/check.sh` for any touched Python (note
  PRE-EXISTING failures separately).

## Constraints
Reuse `renderMarketingPrioritization`, `renderMarketingSignals`, the signal tree, and both existing APIs —
DO NOT fork or reimplement. Lossless (no data/route deletion — relocate + alias, don't drop). Isolated
worktree + own test DB. Do NOT touch `composer-v5.js` (parallel composer work). **Do NOT merge** — report
branch + SHA + worktree + screenshots (unified page, nav rail, an old deep-link resolving). Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
