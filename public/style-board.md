# Artemis Style Board — Index & Inconsistency Report

Generated from CSS audit of `public/css/` (all subdirectories), `public/style.css`, and `public/artemis-design.css`.

---

## Sections

| # | Section | File(s) |
|---|---------|---------|
| 1 | [Buttons](#buttons) | `css/core/components.css` |
| 2 | [Inputs](#inputs) | `css/core/components.css` |
| 3 | [Cards](#cards) | `css/ui/sessions.css`, `css/core/variables.css` |
| 4 | [Modals & Dialogs](#modals--dialogs) | `css/ui/modals.css` |
| 5 | [Panels & Sheets](#panels--sheets) | `css/ui/sessions.css`, `css/panels/` |
| 6 | [Navigation](#navigation) | `css/ui/sessions.css` |
| 7 | [Status Indicators](#status-indicators) | `css/ui/sessions.css`, `css/ui/status-bar.css` |
| 8 | [Typography](#typography) | `css/core/variables.css`, scattered |
| 9 | [Color Tokens](#color-tokens) | `css/core/variables.css`, `artemis-design.css` |
| 10 | [Spacing Scale](#spacing-scale--radii) | ad-hoc, `css/core/variables.css` (radius only) |
| 11 | [Iconography](#iconography) | `public/icons/`, inline SVG |
| 12 | [Motion](#motion) | `css/core/theme.css`, `css/core/variables.css` |
| 13 | [Data Display](#data-display) | `css/ui/status-bar.css`, `css/core/components.css` |
| 14 | [Form Patterns](#form-patterns) | `css/core/components.css`, `css/ui/sessions.css` |
| 15 | [Feature-Specific Surfaces](#feature-specific-surfaces) | `css/features/` |

---

## Inconsistency Summary

| Category | Count | Details |
|----------|-------|---------|
| Distinct hard-coded hex color literals | **163** | Should be `var(--token)` references |
| Orphaned CSS variable references | **55** | Used in CSS but not defined in any token file |
| Distinct hard-coded border-radius values | **24** | Only 4 radius tokens defined (`--radius` 12px, `--radius-md` 16px, `--radius-lg` 24px, `--radius-xl` 22px) |
| Hard-coded box-shadow definitions | **81** | Not using `var(--shadow-*)` or `var(--surface-shadow-*)` |
| Distinct font-weight values | **7** | 300, 400, 500, 600, 650, 700, 800 — no weight tokens |
| Ad-hoc transition durations | **7** | 0.12s, 0.15s, 0.2s, 0.25s, 0.3s, 0.35s, 0.4s — no duration tokens |

---

## Detailed Findings

### Buttons

- `.btn-primary` uses hard-coded `color: #000` instead of consuming `var(--btn-primary-text)` which IS defined in the token file for both themes.
- `.btn-danger:hover` sets `color: #ff6b6f` (hard-coded) instead of `var(--error)`.
- `.modal-btn-save` inside `.folder-select-row` overrides with `!important` amber values (#c77e2b, #a8661e) that diverge from `var(--accent)`.
- 7 distinct class names used as aliases for `.btn-primary` (selector list in `components.css`) — future additions risk being missed.

### Inputs

- `textarea` defaults to `font-family: var(--font-mono)` while all text inputs use `var(--font-sans)` — inconsistent baseline.
- `.modal form textarea` also forces `font-mono` — reinforces the mono pattern for textareas but it is undocumented.
- `.input-compact` and `.input-mono` modifier classes exist but are not used consistently; most inputs are styled with inline padding overrides instead.

### Modals & Dialogs

- Base `.modal` uses `border-radius: var(--radius-lg)` = 24px.
- `.add-project-modal` overrides to `border-radius: 18px !important` — drift from the token.
- `.add-project-modal` suppresses the `::before` accent line via `display: none !important` — inconsistent modal identity.
- `.claude-md-modal` and `.skill-edit-modal` introduce local tokens (`--ink-1`, `--ink-5`, `--border-1`, `--bg-2`, `--accent-alpha`) that are orphaned.

### Status Indicators

- "Degraded" state uses hard-coded `#f0a54a` in two separate files (`sessions.css` L83, `status-bar.css` L82) — should be a shared token like `--warning-amber`.
- `--warning` token resolves to `var(--accent)` (#D4891A) making it identical to the primary accent — no semantic separation between "warning" and "brand".
- `--secondary` = `--error` = `#C94A1F` (rust red) — two tokens with different semantic intent resolve to the same value.

### Typography

- No explicit type scale defined as tokens. Font sizes found: 8.5px, 9px, 9.5px, 10px, 11px, 12px, 12.5px, 13px, 14px, 16px, 20px, 22px.
- Font weights: 300, 400, 500, 600, 650, 700, 800 — the value `650` appears in one feature file (non-standard CSS).
- `--font-display` is defined as an alias for `--font-sans` (both = Inter) — the alias adds complexity without differentiation.
- Label pattern (10px 600 uppercase 0.08em) is duplicated in at least 4 different CSS files instead of being extracted to a utility class.

### Color Tokens

- 163 distinct hex color literals found across all CSS files in addition to token references.
- 55 orphaned `var(--*)` references (used but never defined in any loaded CSS file):
  - `--accent-2`, `--accent-alpha`, `--accent-bg`, `--accent-ink`
  - `--amber-deep`, `--amber-muted`, `--amber-soft`, `--amber-wash`
  - `--bg-2`, `--bg-card`, `--bg-base`, `--bg-hover`, `--bg-input`, `--bg-overlay`, `--bg-primary`
  - `--border-1`, `--border-color`, `--border-color-faint`
  - `--fg`, `--fg-default`, `--fg-muted`
  - `--green`, `--green-wash`, `--red`, `--red-wash`
  - `--ink-1`, `--ink-3`, `--ink-4`, `--ink-5`
  - `--r-full`, `--r-lg`, `--r-md`, `--r-sm`, `--r-xl`, `--radius-sm`
  - `--surface-2`, `--surface-3`, `--surface-base`, `--surface-elevated`, `--surface-hover`, `--surface-input`
  - `--text-primary`, `--text-rgb`, `--text-sm`, `--text-tertiary`, `--text-xs`
  - `--writing-ink`, `--writing-line`, `--writing-list-depth`, `--writing-muted`, `--writing-panel`, `--writing-soft`
  - `--color-success`, `--danger`, `--info`, `--card-status-tint`
  - `--day-grid-row-h`, `--dp-chat-mark-opacity`, `--dp-chat-mark-proximity`, `--h`

### Spacing Scale & Radii

- No spacing tokens defined. Spacing is entirely ad-hoc per component.
- Radius tokens: `--radius` (12px), `--radius-md` (16px), `--radius-lg` (24px), `--radius-xl` (22px).
  - **`--radius-xl` (22px) < `--radius-lg` (24px)** — the xl variant is numerically smaller than lg.
- 24 distinct hard-coded pixel values for `border-radius` exist across CSS alongside token references.

### Iconography

- 6 PNG app icons in `public/icons/` (for PWA/favicon use only).
- UI icons: mix of inline SVG, Unicode glyphs (✎, ×, ⚙, +, …), and emoji.
- No icon size tokens (`--icon-sm`, `--icon-md`, etc.).
- No centralized SVG sprite or icon component system.

### Motion

- 3 easing tokens: `--ease-out-expo`, `--ease-spring`, `--ease-smooth`.
- 14 named `@keyframes` across `theme.css`, `modals.css`, `status-bar.css`.
- `fadeInUp` (translateY 8px) and `slideUp` (translateY 10px) are near-identical — likely duplication.
- No duration tokens. 7 distinct transition durations used across CSS.
- No `prefers-reduced-motion` media query anywhere in the CSS.

### Feature-Specific Surfaces

- `css/features/writing-studio.css`: introduces 6 local tokens (`--writing-*`) that are never defined → orphaned.
- `css/features/okr.css`: uses `--card-status-tint`, `--r-sm`, `--r-md`, `--r-lg`, `--r-xl`, `--r-full` — all orphaned.
- `css/ui/modals.css`: uses `--ink-1`, `--ink-5`, `--border-1`, `--bg-2`, `--accent-alpha` — all orphaned.
- `css/ui/sessions.css`: uses `--amber`, `--amber-ink`, `--amber-wash`, `--amber-deep`, `--amber-soft` — partially defined in `artemis-design.css` but `--amber-deep` and `--amber-muted` are orphaned.

---

## Files Audited

```
public/style.css
public/artemis-design.css
public/css/core/reset.css
public/css/core/variables.css       ← 84 token definitions
public/css/core/theme.css
public/css/core/components.css
public/css/core/layout.css
public/css/core/liquid-glass.css
public/css/core/liquid-glass-overrides.css
public/css/core/responsive.css
public/css/core/print.css
public/css/ui/modals.css
public/css/ui/sessions.css
public/css/ui/status-bar.css
public/css/ui/settings.css
public/css/ui/messages.css
public/css/ui/parallel-panes.css
public/css/ui/commands.css
public/css/ui/context-gauge.css
public/css/ui/file-picker.css
public/css/ui/image-attachments.css
public/css/ui/input-history.css
public/css/ui/notification-bell.css
public/css/ui/permissions.css
public/css/ui/toolbox.css
public/css/ui/worktree.css
public/css/features/agent-monitor.css
public/css/features/agent-sidebar.css
public/css/features/agents.css
public/css/features/analytics.css
public/css/features/background-sessions.css
public/css/features/calendar-page.css
public/css/features/cost-dashboard.css
public/css/features/dev-project-files.css
public/css/features/home.css
public/css/features/jira-board.css
public/css/features/marketing-os.css
public/css/features/okr.css
public/css/features/operations.css
public/css/features/page-layout.css
public/css/features/setup.css
public/css/features/telegram.css
public/css/features/tour.css
public/css/features/voice-input.css
public/css/features/welcome.css
public/css/features/writing-studio.css
public/css/panels/assistant-bot.css
public/css/panels/dev-docs.css
public/css/panels/file-explorer.css
public/css/panels/mcp-manager.css
public/css/panels/memory.css
public/css/panels/skills-manager.css
public/css/panels/tips-feed.css
public/css/panels/tips-feed-glass-options.css
public/css/artemis-shell-overrides.css
```
