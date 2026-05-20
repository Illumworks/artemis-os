# O5 — Builder Breadcrumb + Nav Polish

**Owner:** Codex (paste-ready) OR Sonnet Worker — this is mechanical UI work, no judgment calls
**Branch:** `worker/o5-builder-nav-polish` (or Codex paste branch)
**LOC budget:** ~150 (full-diff insertions; cap at ~200 with headroom)
**Brief author:** Lead (Opus 4.7)
**Depends on:** O1/O2/O3 (already merged at `4ead96a`). No other deps.
**Grounded in:** Existing `public/js/features/agent-builder.js` (636 lines), `public/css/features/agent-builder.css` (449 lines), `public/js/features/agents.js` (1516 lines). Light DOM, Web Components pattern per CLAUDE.md.

## Why this brief exists

After O1/O2/O3 shipped, the Agent-Builder is a conversational surface and the Agent Card is a detail surface — but the navigation between them is bare. Today: clicking into the Builder loses the "back to agents list" affordance, the URL doesn't reflect which agent is being edited, and refreshing mid-edit drops the user back on a generic landing state. These are not big problems; they are the kind of small friction that makes the UI feel half-built. O5 closes that gap.

## Scope

### In scope

1. **Breadcrumb bar at the top of the Builder surface.**
   Structure (verbatim DOM):
   ```html
   <nav class="builder-breadcrumb" aria-label="Breadcrumb">
     <a href="#/agents" class="builder-breadcrumb__crumb">Agents</a>
     <span class="builder-breadcrumb__sep" aria-hidden="true">›</span>
     <span class="builder-breadcrumb__crumb builder-breadcrumb__crumb--current">{agentName || "New Agent"}</span>
     <span class="builder-breadcrumb__sep" aria-hidden="true">›</span>
     <span class="builder-breadcrumb__crumb builder-breadcrumb__crumb--current">Builder</span>
   </nav>
   ```
   Mount inside the existing Builder root element, BEFORE the chat thread / card area. Do not create a new wrapper around the whole Builder — surgical insertion only.

2. **"Back to Agents" affordance** — the first crumb is a real anchor (`href="#/agents"`). Clicking it returns to the agents list. If there's unsaved state in the Builder, intercept the click with `event.preventDefault()` and show a confirm dialog: `"Discard unsaved changes?"`. If confirmed, navigate; otherwise stay.
   - Detection of "unsaved state": if `builderSession.has_pending_changes === true` per the existing store state. If that field doesn't exist, look for `definition_proposals` with status `pending` for the current session. Worker picks the right hook; the criterion is "changes the user hasn't seen committed yet."

3. **URL reflects current agent.**
   - Builder URL becomes `#/builder/{agent_id}` (existing was probably `#/builder` or `#/agents/builder` — Worker confirms).
   - Refreshing on `#/builder/marketing.scout.starbridge_researcher` lands the user back in the Builder for that agent, with the conversation history loaded.
   - Use the existing routing pattern in `public/js/core/router.js` (or wherever the hash routing lives — survey first).
   - New-agent flow stays at `#/builder` (no agent_id) until the first proposal materializes; then the URL updates without a page reload via `history.replaceState`.

4. **Sticky breadcrumb** — `position: sticky; top: 0;` with appropriate background and z-index so it stays visible while scrolling long conversations. Match the existing app's sticky-header pattern (look at how the Daily Brief or Meetings overview does it; do not invent new visual treatment).

5. **CSS** — add a `--builder-breadcrumb-*` block in `public/css/features/agent-builder.css`. Use existing design tokens (`--text`, `--muted`, `--accent`, `--surface-1`, etc.) — no hardcoded hex. The separator (`›`) is muted; current crumb is normal weight; link crumbs are slightly underlined on hover.

6. **Tests** — frontend Web Component tests under `tests/unit/frontend/` for the breadcrumb component if it's extracted into a Custom Element. If it's just inline DOM in the Builder, no new test file is needed; an integration test in `tests/unit/frontend/agent-builder.test.js` (or wherever the Builder tests live) covers the three render states (no agent, named agent, unsaved state).

### Out of scope

- Visual redesign of the Builder surface beyond the breadcrumb. The Style redo pass (task #12) is deferred.
- Mobile/responsive tweaks to the breadcrumb. Match whatever the rest of the app does at narrow widths; do not add new media queries.
- Server-side changes. URL routing is fully client-side.
- The Agents list page itself (where the breadcrumb links to). Out of scope.

## Why this is a Codex candidate

This brief is **mechanical** by design:
- Exact DOM structure is provided verbatim.
- CSS uses existing tokens; no design judgment.
- Routing pattern follows existing convention; no new architecture.
- Unsaved-state detection criterion is given.
- No new API endpoints.
- No new data shapes.

Codex can take this as a paste-ready brief and ship it without architectural calls. If Codex hits a judgment fork (e.g., "the existing routing pattern is inconsistent across modules"), stop and flag — don't choose.

If routed to a Sonnet Worker instead: same brief, no changes needed.

## Invariants

1. **Light DOM, no Shadow DOM.** Per CLAUDE.md — existing convention.
2. **No new design tokens.** Use what's in the theme.
3. **No bundler changes.** Project loads modules in declaration order from `main.js`; if the breadcrumb is a Custom Element, register it in the appropriate `main.js` import chain.
4. **No regression in existing Builder tests.** Run them; if anything breaks, fix it before submitting.
5. **No `localStorage` or `sessionStorage`** for nav state. URL is the source of truth.

## Files expected

- `public/js/features/agent-builder.js` — surgical edits to mount breadcrumb + URL sync. ~50 LOC delta.
- `public/css/features/agent-builder.css` — breadcrumb styles. ~40 LOC.
- `public/js/components/builder-breadcrumb.js` — optional Custom Element if Worker prefers extraction. ~50 LOC.
- `tests/unit/frontend/agent-builder.test.js` (or new test file) — render states. ~30 LOC delta.

Total: ~170 LOC. Worker keeps it at or under 200.

## Test plan

1. **No agent loaded:** crumb says `Agents › New Agent › Builder`.
2. **Named agent loaded:** crumb says `Agents › Starbridge Researcher › Builder`.
3. **Click "Agents" crumb with clean state:** navigates to `#/agents`.
4. **Click "Agents" crumb with unsaved state:** confirm dialog appears; cancel → stays; confirm → navigates.
5. **URL update on agent select:** loading an existing agent updates the URL to `#/builder/{agent_id}` without page reload.
6. **Refresh persistence:** refresh on `#/builder/{agent_id}` re-renders the Builder for that agent.

## Invariants Worker/Codex must NOT regress

- No `git push`.
- `pwd && git branch --show-current` before state-changing Bash.
- `git diff --stat` for LOC self-reporting.
- Existing Builder tests pass.
- No new dependency added.

## What "done" looks like

1. Breadcrumb visible at top of Builder for both new and existing agents.
2. URL reflects current agent; refresh restores state.
3. Unsaved-state confirm dialog prevents accidental nav loss.
4. CSS uses existing tokens; visually matches app style.
5. Existing tests pass; the 6 new test scenarios pass.
6. Full-diff insertions ≤ 200.

## Report submitted

1. `git diff --stat` output.
2. Screenshot or description of breadcrumb in three states (no agent / named agent / unsaved state).
3. URL pattern after agent select (paste).
4. Test pass count.
5. Branch + worktree path.

---

**Lead notes (not for Worker/Codex):**
- This is morale armor: small UI gaps make every other improvement feel weaker. Closing them keeps the Builder feeling like a real product.
- If Codex finds the routing pattern is genuinely inconsistent (some modules use hash, some pushState, etc.), stop and flag for Lead — that's a separate cleanup brief, don't roll it into O5.
