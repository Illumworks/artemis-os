# O1a — Agent-Builder refresh persistence + session list re-fetch + conversation rehydration

**Owner:** Worker (Sonnet)
**Scope:** ≤ **150 LOC**, frontend-only. Half-day or less. Three small bug fixes on shared root cause: the frontend isn't trusting/reading backend state on reload.
**Depends on:** O1 Agent-Builder (already shipped — `builder_sessions` table, `GET /api/builder/sessions`, and `GET /api/builder/sessions/{id}` endpoints already exist and behave correctly).
**Blocks:** Nothing. Polish that closes a recurring frustration Jon hit empirically during the O1 kill-criterion test.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why this brief exists

Jon ran the empirical kill-criterion test for the Agent-Builder. The builder logic worked. The **frontend state-persistence** around the builder did not. Three bugs surfaced in a single session, each driven by the same anti-pattern: the frontend has a local view of state that doesn't get re-synchronized with the backend on reload / mount / URL-load. The backend is doing the right thing in all three cases — the UI just isn't reading it.

This is annoying on its own; it's worse because it undermines confidence in the Builder itself. The agent feels "lossy" when in fact the data is persisted and the UI is the thief.

## The three bugs

### Bug 1 — Refresh defaults to Dev Projects instead of last view

**Symptom:** User is on `/operations/agents/builder/{session_id}`, hits browser refresh, lands on Dev Projects (the app default route).

**Root cause:** Frontend route persistence isn't writing "last active view" to localStorage on navigation. On reload, the boot path falls through to the default route.

**Fix approach:** Write current route to localStorage on every navigation (hash change, programmatic nav, history push). On app boot, read it back and restore — *unless* the URL the user landed on is more specific than the stored route, in which case the URL wins. Skip persisting routes that obviously shouldn't be a "default": 404s, OAuth callback bounces, error pages.

### Bug 2 — Abandoned/deleted Builder sessions come back after refresh

**Symptom:** User abandons or deletes a builder session. It disappears from the left-rail session list. After refresh, it reappears (with `status="abandoned"`) sitting in the list.

**Root cause:** Frontend session list is reading from a stale localStorage cache and not re-fetching `/api/builder/sessions` on Builder page mount. The backend correctly persists the abandonment — the UI just keeps showing the pre-abandonment snapshot.

**Fix approach:** On Builder mount, always re-fetch the session list from backend. Treat backend as source of truth; localStorage cache (if kept) only as a paint-before-fetch optimization that is replaced as soon as the fetch returns. Filter `status === 'abandoned'` from the visible list (or render with clear "abandoned" styling — pick the smaller-LOC option; default to filtering).

### Bug 3 — Conversation history disappears on refresh

**Symptom:** User has a conversation with the Builder, refreshes, the same session opens with an empty message list. As soon as the user sends a new message, only that new message and reply appear. The prior turns are gone from the UI.

**Root cause:** Frontend isn't hydrating the message list from `GET /api/builder/sessions/{id}` on session-load. Backend persists the conversation in the `builder_sessions.conversation` JSONB column; the frontend simply doesn't read it on mount / URL-load.

**Fix approach:** On session selection (whether from list click or URL-load on cold boot), call `GET /api/builder/sessions/{id}`, hydrate the message list from `response.conversation`. Use the same render path that in-flight messages flow through — don't fork a separate "historical message" component.

## Investigation pointers

```bash
# Locate the Builder frontend feature file and its route handling:
grep -rn "agent-builder\|/operations/agents/builder\|builder_sessions" public/js/ | head -20

# Reference implementation: how does Dev Projects v3 persist route + selection?
grep -rn "localStorage" public/js/features/dev_projects.js public/js/features/agent-builder.js | head -20

# Confirm the backend already returns full conversation on the session detail endpoint:
grep -rn "GET.*builder/sessions/{" artemis/builder/routes.py
```

Dev Projects v3 is the reference for route + selection persistence done right. Mirror its pattern; don't invent a new one.

## Acceptance criteria

- [ ] Refresh on `/operations/agents/builder/{session_id}` lands back on the same session with the same conversation visible.
- [ ] Refresh on any other route lands back on that route (Calendar refreshes to Calendar, Meetings to Meetings, Focus to Focus, etc.). Test at least three non-Builder routes.
- [ ] Abandoned or deleted sessions do not return after refresh.
- [ ] The session list always reflects backend truth. **Test:** terminal-Lead deletes a session via SQL (`DELETE FROM builder_sessions WHERE id = ?;`), browser refreshes, deleted session is gone from the list with no soft-undo behavior.
- [ ] Conversation history loads on session selection from URL. **Test:** copy a session URL from one tab, paste into a fresh tab, full history renders before any new message is sent.
- [ ] No regression on in-flight message rendering. Sending a new message still streams and appends correctly.
- [ ] No regression on the default landing route for first-time users with empty localStorage.

## Hard constraints

- **Total scope cap: 150 LOC.** Frontend-only. If you find yourself over budget, stop and report — don't extend.
- **No backend changes.** The backend already does the right thing for all three bugs. If you think the backend is wrong, you've misdiagnosed — re-read the relevant route handler.
- **Single commit on `lead/j6a-granola-integration` directly.** This is a small bug-fix bundle, not feature work — no worker branch needed.
- **CWD-trap defensive reflex before commit:**
  ```bash
  pwd && git rev-parse --show-toplevel && git branch --show-current
  ```
  Expected: main worktree on `lead/j6a-granola-integration`. If `pwd` shows `.claude/worktrees/agent-*`, `cd` back to `/Users/artemis/Desktop/Artemis/artemis-os` before committing.
- **`git diff --staged` before commit.** Read it twice. Confirm no stray files, no debug logging, no backend changes leaked in.
- **Commit message (verbatim):**
  ```
  fix(o1a): builder route persistence + session list re-fetch + conversation rehydration
  ```

## Where to start

1. **Read the reference.** Open `public/js/features/dev_projects.js` and find how it persists the current route / selected project. That's the pattern to mirror.
2. **Locate Builder feature file.** Likely `public/js/features/agent-builder.js` (confirm via grep). Read it end-to-end before changing anything.
3. **Bug 1 first** (route persistence). Smallest change, biggest UX recovery. Land it, smoke-test refresh on three different routes including the Builder URL.
4. **Bug 2 second** (session list re-fetch). On Builder mount, replace any "read from cache" path with an unconditional fetch + render. Smoke-test with the SQL deletion case.
5. **Bug 3 third** (conversation rehydration). On session selection from URL or click, call `GET /api/builder/sessions/{id}` and hydrate from `response.conversation`. Smoke-test the copy-URL-paste-fresh-tab case.
6. **Quality gate.** Stage, `git diff --staged` twice, LOC count under cap, smoke evidence pasted in report.
7. **CWD-trap check, then commit.**

## Quality acceptance gates

- [ ] Manual smoke output pasted verbatim in your report for each of the five acceptance criteria above.
- [ ] LOC count of staged diff under 150. Paste the number.
- [ ] `git diff --staged` read twice before commit.
- [ ] `pwd && git branch --show-current` confirmed before commit.
- [ ] No backend file in the staged diff. Confirm by listing changed paths.
- [ ] No regression on the default landing route for first-time users (test: clear localStorage, reload, app boots to the expected default).

---

## Paste-ready Worker prompt

```
You are a Worker (Sonnet) implementing brief `briefs/o1a-builder-refresh-persistence.md` in the artemis-os repo. Read the brief end-to-end before touching any code.

Three frontend state-persistence bugs in the Agent-Builder:
1. Refresh on a builder session URL drops you on Dev Projects instead of restoring the session.
2. Abandoned/deleted builder sessions reappear in the left-rail list after refresh.
3. Conversation history is empty when a builder session loads from URL on cold boot.

All three share one root cause: the frontend isn't trusting/reading backend state on reload/mount/URL-load. Backend is correct — fix is frontend-only.

Hard constraints:
- ≤ 150 LOC, frontend-only. No backend changes.
- Single commit on branch `lead/j6a-granola-integration` directly.
- Before commit run: `pwd && git rev-parse --show-toplevel && git branch --show-current` — expected main worktree on `lead/j6a-granola-integration`. If pwd is under `.claude/worktrees/agent-*`, cd back to `/Users/artemis/Desktop/Artemis/artemis-os` first.
- Before commit run `git diff --staged` and read it twice.
- Commit message verbatim: `fix(o1a): builder route persistence + session list re-fetch + conversation rehydration`

Reference implementation for route persistence: `public/js/features/dev_projects.js` (Dev Projects v3). Mirror its pattern.

Builder feature file: locate via `grep -rn "agent-builder" public/js/`.

Backend endpoint that returns full conversation: `GET /api/builder/sessions/{id}` — already implemented, just call it on session-load.

When done, report each acceptance criterion with verbatim smoke evidence, staged LOC count, and commit SHA.
```
