# Dev Projects v3 — port Claude Code Desktop chat UI (stop redesigning)

**Owner:** Worker (Sonnet)
**Scope:** ~300-500 LOC frontend rewrite + CSS port. Half-day to a day.
**Depends on:** Existing Dev Projects v2 backend (untouched in this brief — backend is already correct).
**Blocks:** Polishing further surfaces that should adopt the same chat-UI primitives.

> All file paths in this brief are relative to the repo root. The harness controls the worktree.

## Why this brief exists (read this carefully — it's the most important section)

Codex's v2 rebuild of Dev Projects shipped a custom chat-page chrome that has drifted across two follow-up patches and still doesn't match what the user wants. The user's feedback, verbatim: *"we shouldnt be reinventing this section (UI wise) when node version worked. the only new addition is the right rail."*

The reference is the **Claude Code Desktop chat UI** (the "claudeck" interface — the same app the user runs locally to talk to Claude). That UI is mature, opinionated, and works. We are porting it, not reinventing it.

**This brief is a port, not a redesign.** If you find yourself improving on the Claude Code Desktop layout, stop. The win here is fidelity, not innovation. Net-new affordances are limited to one thing: a right-side preview rail (see Slice C).

## Reference: the layout to port

The Claude Code Desktop chat page looks like this:

```
┌──────────────────┬─────────────────────────────────────────────────────────┐
│ [sidebar with    │  [breadcrumb] / [session title]              [icons →] │ ← thin top bar (≤44px)
│  projects +      ├─────────────────────────────────────────────────────────┤
│  sessions —      │                                                         │
│  already shipped]│           [messages, scrolling, anchored to bottom]     │ ← main scroll area
│                  │                                                         │
│                  │                                                         │
│                  │                                                         │
│                  ├─────────────────────────────────────────────────────────┤
│                  │  [composer — fixed at bottom, always visible]           │ ← composer
│                  │  ┌──────────────────────────────────────────────────┐   │
│                  │  │ Ask a question, plan a task, or start a repo...  │   │
│                  │  └──────────────────────────────────────────────────┘   │
│                  │  [+] [mic]                            [Opus 4.7]  [↑]   │
└──────────────────┴─────────────────────────────────────────────────────────┘
```

Key properties (these are not negotiable):

1. **Top bar is thin and information-dense.** A single row, ~44px tall. Contains: breadcrumb-style project / session title on the left (clickable to switch session), and a tight row of icon buttons on the right (model selector, settings cog, optionally a right-rail toggle).
2. **No "empty-state" panel that expands to fill vertical space.** The scroll area is anchored to the bottom; when there are no messages, it's just an empty scroll area with the composer pinned at the bottom. **Do NOT add seed chips, project-aware copy, or a centered empty-state visual.** The composer is the call-to-action. Period.
3. **Composer is fixed at the bottom of the viewport.** Not floating, not pushed by content above it. Always reachable.
4. **No duplicate controls.** One model picker (in the top bar or in the composer's right edge, your call — match Claude Desktop). One project switcher (the breadcrumb).
5. **The "Run in parallel" / `[⬛][⫾⫾][⫾⫾⫾⫾]` toggle that's currently visible** — leave it where it is OR remove it from Dev Projects entirely if it's not meaningful for project sessions (it may have been inherited from a different surface — check before removing). When in doubt, hide it for v3 and re-evaluate.

## The custom v2 chrome to remove

These were added by Codex v2 and the polish patches. **Delete them all** in v3:

- The empty-state "Ask anything about [project], or pick up where you left off" + seed chips ("Plan a feature", "Debug an error", "Review the codebase"). Net-negative — they make the page feel cluttered when nothing is happening.
- Any decorative/branded visual elements in the chat scroll area (the watermark, the centered icon, etc.).
- The "Annotations" toggle in the top bar — replaced by Slice C's right-rail toggle (which only renders in single-session mode).
- The dual project-name display ("vanilla-portal >" breadcrumb at top-left AND any other project-name indicator) — keep ONE.
- The currently-hidden duplicate model `<select>` element kept in DOM for backward compat (per the previous polish patch). Delete it. If `loadModels()` / `syncPickerFromSession()` need a new mount point, give them one inside the new top-bar model picker.

## Slice A — top bar (faithful port)

- [ ] Rewrite `public/js/features/dev_projects.js`'s top-bar render. Single row, ~44px tall, matching the Claude Desktop layout described above.
- [ ] Left side: breadcrumb of `[project name] / [session title]`. Project name is clickable, opens the project switcher popover (already wired by D2 fix in commit `c2bdb10` — reuse). Session title is editable inline on double-click (existing behavior — preserve).
- [ ] Right side: tight icon row — model picker, settings cog (Session Config), right-rail toggle (Slice C). No "Anthropic" label, no separate provider chip, no "CLAUDE.md" chip, no "Run in parallel" toggle in Dev Projects (move it elsewhere if it's needed by other surfaces).
- [ ] Model picker is a small button showing the current model name (e.g. "Opus 4.7") that opens a dropdown on click. Style it to match the rest of the icon row — no big inline `<select>` element.
- [ ] CSS: top bar uses `position: sticky; top: 0;` so it stays anchored when the scroll area scrolls. No transform animations, no resizing on hover.

## Slice B — main scroll area + composer (faithful port)

- [ ] The main column below the top bar is a flex column with `min-height: 0` and `flex: 1`. The messages container is `flex: 1; overflow-y: auto;` and anchors to bottom (use `scrollIntoView({block: 'end'})` after appending, or `display: flex; flex-direction: column-reverse` with reversed message order — pick whichever matches Claude Desktop's actual behavior).
- [ ] Composer is `position: sticky; bottom: 0;` inside the main column. Always visible.
- [ ] No empty-state block. When `messages.length === 0`, the scroll area just renders empty. The composer's placeholder text is the entirety of the empty-state UX. Use the placeholder copy from Claude Desktop: `"Ask a question, plan a task, or start a repo change..."` (or whatever the current Claude Desktop placeholder reads).
- [ ] The "gap at the top moves up and down depending on parallel-mode toggle" symptom should disappear once the empty-state block is removed and the scroll area is properly anchored. Verify this is fixed.

## Slice C — right preview rail (NET NEW — this is the only innovation)

Mirror Claude Code Desktop's right-side preview/inspection rail. When the user clicks a link, opens a file mention, or invokes a preview action in chat, the right rail slides open and shows the preview. Closes when dismissed.

- [ ] **Visibility gate**: the right rail is ONLY available in single-session mode. In parallel-sessions mode (the `[⫾⫾]` / `[⫾⫾⫾⫾]` toggles), the rail's toggle button is hidden and the rail can't be opened. This is the user's explicit requirement: *"should only work if you are using the non parallel sessions i can see how that wouldnt work."*
- [ ] **Toggle**: a small icon button in the top bar's right icon row. Clicking opens/closes the rail. State persists across reload (localStorage).
- [ ] **Contents**: the rail is initially empty when opened with nothing to preview. Display: "Nothing to preview yet. Open a link, file, or annotation to see it here." (Or match Claude Desktop's empty-state copy exactly if it's tighter.)
- [ ] **Width**: ~360-400px, matching Claude Desktop. Resizable via a drag handle on the left edge (optional polish — skip if it adds >30 LOC).
- [ ] **Auto-open conditions**: open automatically when an annotation is created (existing `renderAnnotations` flow from c2bdb10) or when a user action explicitly invokes a preview (e.g., clicking a file mention in a message). Don't auto-open on every render.
- [ ] **Replacing the current "Annotations" panel**: the previous custom Annotations popup (the floating box in the screenshot) is gone. It becomes the contents of the right rail when annotations exist. The "Pick page target / Annotate this page / Send to chat" controls move into the rail.

## Slice D — cleanup

- [ ] Delete the dead `<select>` element kept in DOM "for backward compat" from the previous polish patch. Wire `loadModels()` / `syncPickerFromSession()` to the new top-bar model picker.
- [ ] Verify the legacy `public/js/features/projects.js` click handler on `#header-project-title-btn` no longer double-fires. Either remove the legacy binding or make it explicitly defer to v3.
- [ ] Update `public/css/features/dev-projects.css` to remove rules that are no longer applied. Audit-and-trim — don't leave dead CSS.

## Acceptance — what done looks like

Side-by-side comparison with Claude Code Desktop:

- [ ] Open a Dev Projects session with **zero messages**. The chat area is empty. The composer is at the bottom of the viewport. No seed chips, no empty-state block, no centered watermark, no project-aware copy. Just an empty scroll area + composer.
- [ ] Toggle the parallel-sessions modes (`[⬛] [⫾⫾] [⫾⫾⫾⫾]` if those are still present in Dev Projects). The vertical layout does NOT shift — composer stays anchored, top bar stays anchored.
- [ ] Click the project breadcrumb (`vanilla-portal`). Popover opens listing projects. Click another → switches session. Style matches the rest of the Artemis popover/dropdown vocabulary (not Codex's custom style).
- [ ] Click the model picker button. Dropdown opens listing available models. Picking one updates the session and persists.
- [ ] Click the right-rail toggle. Rail slides open from the right (~360px wide). Empty-state visible inside. Toggle again → closes.
- [ ] Switch to parallel-sessions mode (any of the multi-pane toggles). The right-rail toggle button is hidden. Trying to open the rail via keyboard shortcut (if any exists) is a no-op.
- [ ] Type a message in the composer and send. Message appears in the scroll area, anchored to the bottom. Composer remains at the bottom of the viewport after sending.
- [ ] Refresh the page mid-session. State restores correctly — same session, same model, same rail open/closed state.

## Quality acceptance gates

- [ ] Manual smoke output pasted **verbatim** in your report, including:
  - At least 3 screenshots: empty-state session, populated-session, right rail open
  - A side-by-side comparison call-out of any place where the port deviates from Claude Code Desktop (with reasoning)
- [ ] `git diff --staged` before every commit — twice-bitten pattern in this project. See `briefs/CONVENTIONS.md` "Commit Discipline" section.
- [ ] `pwd && git branch --show-current` before committing — CWD-trap is real, see `briefs/CONVENTIONS.md` "CWD trap" section. Confirm you're on `lead/j6a-granola-integration` in the main worktree, not under `.claude/worktrees/`.
- [ ] No browser console errors after page load
- [ ] No regression on other surfaces — open Calendar, Meetings, Focus, Jira Board after the rebuild and confirm they still render

## Out of scope (separate briefs)

- Backend changes (the existing `/api/dev-projects/*` routes are correct — don't touch them)
- Slack panel (separate concern, J9c being worked elsewhere)
- Connectors UI for `needs_reauth` (J10d, separate)
- Right-rail content beyond annotations (file previews, image previews, code previews) — those become follow-up Slices E, F, G once Slice C lands
- Resizable right rail with drag handle (optional polish — only do it if it adds <30 LOC)

## Where to start

1. Read this brief twice. Note the "this is a port, not a redesign" framing — internalize it before touching code.
2. Open Claude Code Desktop on your machine. Use it for 5 minutes. Pay attention to: top-bar height, empty-state behavior, composer position, right-rail behavior. **The port should look like this.**
3. Read the current Dev Projects v2 implementation:
   - `public/js/features/dev_projects.js` (largest file — the custom v2 chrome lives here)
   - `public/js/components/dev-projects-*.js` (component layer)
   - `public/css/features/dev-projects.css` (styles — will need significant trim)
4. Plan the rewrite:
   - Slice A (top bar) — replace the existing top bar
   - Slice B (scroll + composer) — remove empty state, fix anchoring
   - Slice C (right rail) — replace annotation popup with rail
   - Slice D (cleanup) — delete dead code
5. Implement. Run the manual smoke. Take screenshots. Report.

## Notes on what previous attempts got wrong (so this one doesn't repeat)

- **Codex v2** shipped a custom sidebar pattern that was solid (left rail is fine — don't touch it) BUT also shipped a custom chrome layout that diverges from Claude Desktop. v3 keeps the sidebar, replaces the chrome.
- **The first polish patch** (commit `c2bdb10`) added seed chips, an empty-state block, and a popover for the project switcher. **Delete the seed chips and empty-state block in v3.** Keep the popover but restyle to match Artemis's general dropdown vocabulary.
- **The "Annotations" feature** got built as a custom popup. v3 replaces it with the right-rail pattern.
- **No agent has ported the Claude Desktop layout verbatim yet.** v3 is the first attempt to do that. Bias hard toward fidelity over creativity.
