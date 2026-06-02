# Dead Stylesheet Audit

Generated: 2026-05-19
Auditor: Claude Sonnet sub-agent
Branch: `lead/j6a-granola-integration`

## TL;DR

Four CSS files in `public/css/` are completely unreachable from the served bundle, totaling **~2,871 LOC of dead stylesheets**. The biggest culprit is `liquid-glass-overrides.css` (1,420 LOC), which was commented out of `public/style.css` along with `liquid-glass.css` (961 LOC) when the legacy Claudeck "liquid glass" chrome was retired — both are still on disk and a sub-agent recently "fixed" a rule inside one of them with zero runtime effect. The most dangerous dead file is **`public/css/panels/file-explorer.css`** (324 LOC) — it is never imported anywhere, but its `.file-tree-item`/`.file-explorer-*` selectors are still emitted at runtime by `public/js/panels/file-explorer.js`, so a future agent who greps for those selectors will trip the same trap. There is also one broken (non-dead but invalid) import: `style.css:55` `@import url("css/features/retro-terminal.css")` points at a file that does not exist on disk. Recommendation: delete all 4 dead files plus the broken import, in a single low-risk commit.

## Methodology

Built two sets and diffed them.

**Defined set**: every `*.css` file under `public/` at depth ≤ 3 (excluding HTML fixtures), via `find public/css public -maxdepth 3 -name "*.css"`.

**Loaded set**: starting from `public/index.html`, walked the dependency graph of `<link rel="stylesheet">` tags plus every uncommented `@import url(...)` inside reachable files. `public/style.css` aggregates 45 modules via `@import`; `index.html` directly links 9 additional non-`style.css` sheets (`artemis-design.css`, `artemis-shell-overrides.css`, `panels/assistant-bot.css`, `panels/floating-artemis.css`, `ui/parallel-panes.css`, `features/calendar-page.css`, `features/marketing-os.css`, `features/integrations.css`, `features/dev-projects.css`). `public/login.html`, `public/offline.html`, and `public/style-board.html` were checked separately — `style-board.html` is an internal style-preview fixture not served as part of the main app, and loads everything individually; it was excluded from the "loaded" set for the purpose of the audit (its files are all already loaded by index.html). No `@import` chains beyond the first level were found inside the imported modules. Dead = defined − loaded.

## Confirmed dead files

### public/css/core/liquid-glass-overrides.css
- **Status**: Never reached. `@import` is explicitly commented out at `public/style.css:12`.
- **Last modified (git)**: 2026-05-16, commit `b08d887` (`feat(ui): Phase E1 — serve Node frontend assets from FastAPI`).
- **LOC count**: 1,420
- **Why it's dead**: Disabled alongside `liquid-glass.css` when the legacy Claudeck chrome was retired. The comment block at `style.css:5-10` explains the parent file (`liquid-glass.css`) was killed because its global `button, .btn` rule "stamped every button in the shell with a blurred-glass pill … that fights the approved artemis-os treatments." The overrides file went out with it.
- **Selectors that look live (risk)**: HIGH. Hundreds of selectors that match real DOM in the running app — `.chat-area`, `.top-header`, `.header-dropdown`, `.header-dropdown-menu`, `.sidebar-toggle-btn`, `.parallel-container`, plus dark-mode variants. A grep for any of these finds this file first because it's enormous and selector-dense. **This is exactly the trap that caused today's wasted sub-agent edit.**
- **Recommendation**: **delete**. Anything still relevant has long since been re-implemented in `artemis-shell-overrides.css` or feature-specific files.

### public/css/core/liquid-glass.css
- **Status**: Never reached. `@import` is explicitly commented out at `public/style.css:11`.
- **Last modified (git)**: 2026-05-16, commit `b08d887`.
- **LOC count**: 961
- **Why it's dead**: Same retirement event as above. The comment at `style.css:5-10` documents the reason in plain English. `artemis-shell-overrides.css:23` and `:233` also reference this file by name in comments, explaining historical workarounds — those comments will become stale once the file is deleted but are otherwise harmless.
- **Selectors that look live (risk)**: HIGH. Defines `.glass-base`, `.top-header`, `.sidebar`, `.session-list li`, plus a global `body` and `body::before/::after` rule. Many of these selectors still exist in the live app and are now styled by other files.
- **Recommendation**: **delete**. Also clean the stale comment references inside `artemis-shell-overrides.css` in the same commit.

### public/css/panels/file-explorer.css
- **Status**: Never reached. No `@import` and no `<link>` in any served HTML references it. Grep across `public/**` confirms zero references in CSS, HTML, or JS.
- **Last modified (git)**: 2026-05-16, commit `b08d887`.
- **LOC count**: 324
- **Why it's dead**: No record of when the import was removed (file was already orphaned at the time of the `b08d887` ingest). Appears to have been an isolated panel stylesheet that was decoupled from the panel JS at some point and never re-wired.
- **Selectors that look live (risk)**: **HIGH — most dangerous file in this audit.** `public/js/panels/file-explorer.js` is still in the bundle and emits `.file-tree-item`, `.file-tree-item.active`, `.file-search-result`, `.file-explorer-toolbar`, `.file-explorer-search`, and `.file-refresh-btn` at runtime (see `file-explorer.js:54, 135, 181, 240, 414`). So the JS-rendered file explorer is unstyled or relying on cascading defaults, and a future agent debugging "why does the file tree look broken" will grep, find this dead file, "fix" it, and see nothing change.
- **Recommendation**: **decision required.** Either restore the import (if file explorer is supposed to look like this CSS describes) or delete the file outright. The right answer depends on whether the file explorer panel is still a supported surface in artemis-os post-Phase E.

### public/css/panels/tips-feed-glass-options.css
- **Status**: Never reached. No `@import`, no `<link>`, no JS reference.
- **Last modified (git)**: 2026-05-16, commit `b08d887`.
- **LOC count**: 166
- **Why it's dead**: This was clearly an **A/B design exploration** — 5 numbered variants `.tips-feed-card.glass-option-1` through `.glass-option-5`, each with dark-mode pairs. There is no live DOM emitting `glass-option-N` class names anywhere in `public/js`. The winning variant was presumably folded back into `panels/tips-feed.css` (which IS loaded) and this options sheet was left behind.
- **Selectors that look live (risk)**: LOW. All selectors are gated on `.glass-option-N` modifiers which appear nowhere in the live codebase.
- **Recommendation**: **delete**. Exploration artifact, no longer relevant.

## Loaded-but-suspicious / additional finding

**Broken import — not dead, but invalid.** `public/style.css:55` contains:

```css
@import url("css/features/retro-terminal.css");
```

The target file does not exist on disk. Browsers silently swallow 404s on `@import`, so this is harmless at runtime but wastes one HTTP request per page load and pollutes devtools. Should be removed in the same cleanup commit.

## Recommended cleanup actions

1. **Delete `public/css/core/liquid-glass-overrides.css`** (1,420 LOC).
2. **Delete `public/css/core/liquid-glass.css`** (961 LOC).
3. **Delete `public/css/panels/tips-feed-glass-options.css`** (166 LOC).
4. **Remove the two commented `@import` lines** at `style.css:11-12` (and optionally the now-orphaned multi-line comment at `style.css:5-10`, or rewrite it as a one-liner historical note).
5. **Remove the broken `@import url("css/features/retro-terminal.css")` line** at `style.css:55`.
6. **Update stale comment references** to `liquid-glass.css` inside `public/css/artemis-shell-overrides.css` (lines 23 and 233) so they don't confuse future readers.
7. **Decide on `public/css/panels/file-explorer.css`** — see "Open questions" below. Do NOT silently delete this one; it has matching live DOM.

Total removable LOC if all decisions go "delete": ~2,871 lines across 4 files, plus 3-4 lines in `style.css`. Risk: very low — nothing in this set is currently affecting rendering.

## Open questions for Jon

1. **`file-explorer.css`** — is the file explorer panel still a shipping surface? `public/js/panels/file-explorer.js` is still loaded and emits the matching class names, so either (a) the panel is supposed to look styled and the import was accidentally dropped (in which case **restore** by adding `@import url("css/panels/file-explorer.css");` to `style.css`), or (b) the panel is being phased out and both the CSS and JS should be archived together. The current state — JS live, CSS dead — is the worst of both worlds.
2. **`liquid-glass.css` historical reference** — confirmed safe to delete the file entirely, or do you want it kept in a `_archive/` directory for design reference? The comments in `style.css` and `artemis-shell-overrides.css` document the decision well enough that I'd vote delete, but flagging in case it's worth preserving as a design-history artifact.
