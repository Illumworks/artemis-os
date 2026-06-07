# Inbox UI Placement Fix — Move Inbox panel from sidebar to main Agents page

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/inbox-ui-placement`
**LOC cap:** ~120 (DOM move + hero shrink + a couple selector updates).
**Priority:** HIGH — Proposals Inbox feature shipped but Inbox panel renders into the wrong DOM container. Jon's screenshot of the main Agents page shows hero + roster + profile unchanged; the Inbox is invisible because it's in the left sidebar (`#agent-panel`), not the main Agents page (`renderAgentsPage()` in operations-shell.js).

---

## The bug (precise)

The previous Inbox brief said "Panel at the **top of the Agents page**, before the agent roster" and Jon's standing direction was "yes you can do it at the top of the page, because i always felt the hero currently is to big and not functional."

The Worker instead targeted `$.agentPanel` (i.e. `document.getElementById("agent-panel")`, which is `.agent-sidebar-body` per `public/index.html:494`). That's a **left-rail sidebar container**, not the main Agents page content.

The main Agents page is rendered by `renderAgentsPage()` in `public/js/features/operations-shell.js:1801` — it emits a string of HTML containing:
1. `renderOperationsHero("Agents", "Who does work", "A roster for scanning...", [chips], [buttons])` — the oversized hero
2. `<section class="ops-grid ops-agents-grid">` containing the roster and profile articles

The Inbox panel needs to live **between (1) and (2)**, or replacing part of (1), on the main page — not in the sidebar.

---

## Scope

### Part A — Move the Inbox panel to the main Agents page

In `public/js/features/operations-shell.js`, inside `renderAgentsPage()` (around line 1887-1911), inject a mount point in the returned HTML between the hero and the `<section class="ops-grid">`. Example placement:

```html
${renderOperationsHero(...)}
<section id="agents-inbox-mount" class="agents-inbox-mount"></section>   <!-- NEW -->
<section class="ops-grid ops-agents-grid">
  ...
</section>
```

Then in `public/js/features/agents.js`:

- `renderInboxPanel()` currently targets `$.agentPanel`. **Change it** to target `document.getElementById("agents-inbox-mount")`.
- The function is currently called from `renderAgentPanel()` (the sidebar render function at line ~449). **Remove that call from renderAgentPanel** — the sidebar is not the Inbox's home.
- Add a fresh trigger: after the main Agents page renders (e.g. an `onState("view")` listener in operations-shell.js that fires when view === "agents"), call `renderInboxPanel()` to populate the new mount point.

### Part B — Shrink the existing hero (the part the original brief gave permission for)

The current `renderOperationsHero("Agents", ...)` is "too big and not functional" per Jon. Restructure it so the Inbox is the primary above-the-fold surface:

- The 4 stat chips (Roster, Run health, Skills linked, Memory) can compress into a single denser bar, OR move INTO the Inbox panel as a status row, OR be removed entirely if their info already lives in the roster.
- The 4 action buttons (Build with Agent-Builder / New agent / Edit with Builder / Back to Operations) can collapse into a tighter action row.
- Descriptive subtitle ("A roster for scanning, plus a dedicated main-canvas profile for policy, memory, skills, and runtime health.") can be removed — it's marketing text, not functional.

Use your aesthetic judgment within the existing design system (`public/css/operations.css` or wherever `ops-hero` styles live). Goal: when an operator lands on Agents, the first thing they see is the Inbox panel (what needs attention) — the hero should be a tight title bar at most.

### Part C — Verify

After the change:

1. Hard-reload `http://localhost:8000/#/agents`. The Inbox panel must be visible at the top of the main content area, above the roster, with 11 rows of agents (assuming current DB state — `GET /api/builder/inbox` returns 11 in `agents_with_new_summaries`).
2. The hero should feel materially smaller than before. Send a screenshot.
3. Click "Review" on an Inbox row → opens Builder with `target_id` set, mark-reviewed fires, row drops out on re-render.
4. The sidebar (left rail) should NOT have the Inbox panel anymore.

### Part D — Don't regress

- The existing sidebar-rendered "Builder surfaces" preview banner + Orchestrate / Monitor cards must still render in `#agent-panel` (sidebar). Only the Inbox panel moves out.
- The 30-second cache (`_inboxCache`) and `invalidateInboxCache()` calls stay as-is — only the mount target changes.
- `_openBuilderForAgent()` behavior unchanged (CC18 pattern preserved).

---

## Files owned

- EDIT: `public/js/features/operations-shell.js` (add mount point in `renderAgentsPage()`; trigger `renderInboxPanel()` after view renders; shrink hero)
- EDIT: `public/js/features/agents.js` (change target from `$.agentPanel` to `#agents-inbox-mount`; remove the call from `renderAgentPanel()`)
- EDIT: `public/css/operations.css` or equivalent (hero shrink styling + `.agents-inbox-mount` container if needed; existing `.inbox-panel` CSS in `public/css/features/agents.css` should still work as-is once the panel is in a wider container)

---

## Acceptance criteria

1. Screenshot of `/#/agents` after hard reload — Inbox panel visible at top of main content area, hero shrunk. **Paste.**
2. `git diff --stat` shows changes confined to the files listed above. **Paste.**
3. `./scripts/check.sh` passes modulo known-exempt failures. **Paste.**
4. Sidebar still has Builder surfaces / Orchestrate / Monitor cards (no regression).

---

## Hard constraints

- Don't introduce new visual languages. Use existing design tokens.
- Don't touch the backend (`/api/builder/inbox` route, the migration, the tests) — placement-only fix.
- Don't touch CC18's `target_id` mechanism or the `_openBuilderForAgent` helper — those work and Jon already verified them.
- Local-only git.

---

## Report-back format

```
Inbox UI Placement Fix report
1. Commit / branch / worktree
2. LOC diff stats
3. Screenshot of /#/agents post-fix (paste path or attach)
4. Confirmation: sidebar unaffected, Inbox now in main canvas
5. Hero shrink approach (what you removed / compressed)
6. check.sh summary
7. Anything surprising — especially if you find the original Worker's call sites have additional sidebar dependencies on renderInboxPanel that this brief doesn't anticipate
```

---

**Worker: the Proposals Inbox feature works end-to-end (migration, API, tests, action handlers, CC18 deep-link). The previous Worker placed the panel in the wrong DOM container — left sidebar instead of main page. This is a surgical move + a hero restructure. The original brief was explicit about placement and you should treat that as authoritative.**
