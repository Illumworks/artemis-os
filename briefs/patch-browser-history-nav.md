# Patch — Browser Back/Forward Nav Integration

**Owner:** Codex (paste-ready)
**Branch:** `codex/patch-browser-history-nav`
**LOC budget:** ~120 (honest overrun OK to ~180)
**Depends on:** existing state-driven nav pattern (`setState('view', ...)`).

## Why

App uses state-driven SPA navigation: `setState('view', 'agents/builder')` switches surfaces. Works internally, but **browser back/forward buttons don't navigate** — the state changes don't push to browser history.

User clicks Operations → Agents → marketing.scout.starbridge_researcher → expects back button to return to Agents list. Currently does nothing OR pops to before-app-load.

## Scope

### Wire state changes to browser history

In `public/js/artemis-shell.js` (or wherever the `setState` event dispatcher lives):

1. After every `setState('view', newView)`, call:
   ```javascript
   const url = '#/' + encodeURIComponent(newView);
   history.pushState({view: newView, builderAgentId: <if applicable>}, '', url);
   ```

2. Add a `popstate` listener on `window`:
   ```javascript
   window.addEventListener('popstate', (event) => {
     if (event.state && event.state.view) {
       // Restore state WITHOUT triggering another pushState
       setStateInternal('view', event.state.view, {fromHistory: true});
       if (event.state.builderAgentId) {
         setStateInternal('builderAgentId', event.state.builderAgentId, {fromHistory: true});
       }
     }
   });
   ```

3. Ensure `setState` checks the `fromHistory` flag and skips the pushState call when restoring from popstate (prevents history bloat / infinite recursion).

### Initial hash handling

On page load, check `window.location.hash`:
- If `#/<view>` present: restore that view
- Otherwise: default to current behavior (probably `#/focus` or whatever the landing is)

This makes deep-links work — `https://app.artemisos.me/#/operations/agents` opens directly on Agents.

### Companion state slots (builderAgentId, etc.)

For state slots OTHER than `view` that should appear in URL (e.g., `builderAgentId` per O5):

- Include them in the pushState data object
- Optionally surface as URL query params (`#/operations/builder?agent=marketing.scout.starbridge_researcher`)
- Worker decides if query params are worth the complexity for v1; pushState data alone is enough for browser back/forward to work. URL-as-shareable-link is bonus.

### Tests

- After `setState('view', 'agents')`, `window.location.hash === '#/agents'`
- After back button (popstate), state restored to previous view
- After forward button, state restored to next view
- Deep-link load: navigate to `/#/operations/pipelines` directly → app opens on Pipelines
- No infinite recursion: pushState + popstate don't echo

## Out of scope

- Per-page deeplinking with parameters (e.g., `#/pipelines/123/canvas` opens a specific pipeline). v1 just routes to the page; opening a specific item is via existing in-page state.
- Search params persistence (e.g., `?filter=active`). Defer.
- History entry capping. Browser handles.

## Files expected

| File | LOC |
|---|---|
| `public/js/artemis-shell.js` (or where state setter lives) | ~50 delta |
| `public/js/core/state.js` (if state machine is separate) | ~20 delta |
| `tests/unit/frontend/test_browser_history_nav.py` (new) | ~50 |

**Total: ~120 LOC.** Cap 180.

## Invariants

- node --check on modified JS
- No new dependencies (no router library)
- Existing setState calls don't break
- Existing localStorage state restore still works for non-history-aware state slots
- git switch lead/j6a-granola-integration after commit

## Report

git diff --stat, paste setState integration showing pushState call, screenshots of back/forward working between 3 pages, deep-link load test, test pass count, branch.

---

**Lead notes (not for Codex):**
- This is a small but high-leverage UX fix. Browser back/forward is muscle memory; users hit it constantly. Currently they hit nothing.
- Deep-link support is a nice bonus that lets users bookmark specific pages.
- After this lands, Pipeline canvas → click into Marketing Pipeline → back button returns to Pipelines list.
