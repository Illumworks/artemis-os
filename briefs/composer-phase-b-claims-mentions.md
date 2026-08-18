# Build brief — Composer Phase B: claim click-to-replace + @mention autocomplete + Slack DM

**Agent:** terminal (owns `composer-v5.js` FE) + the backend bits below. **Branch:**
`worker/composer-phase-b` off **current `main`** (pull first). **Own git worktree, cd inside it, own test DB
`artemis_test_phaseb`.** **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md` + the current
`composer-v5.js`.

**⚠️ DO NOT TOUCH the selection-toolbar logic.** It was just rebuilt on ProseMirror's own update cycle
(`dispatchTransaction` → `updateSelectionState`, `view.hasFocus()`, `view.coordsAtPos`, the whole-paper-
editable padding, and `handleOutsidePointerDown`). Leave `updateSelectionState`, `positionNearSelection`,
`showSelToolbar`/`hideSelToolbar`, and the `.cv5-paper`/`.ProseMirror` padding ALONE. You're editing three
OTHER areas: the claim popover, the comment composer, and two backend endpoints.

## 1. Click a "Nearest Approved" card → replace the flagged text (FE, composer-v5.js)
In the claim-flag popover, the "Nearest approved" entries (`.cv5-claim-pop-nearest-item`) are currently
display-only. Make each one **clickable**: clicking it replaces the flagged claim's text in the document with
that approved phrasing, then closes the popover and re-scans (the flag clears because the text now matches an
approved claim).
- The flagged claim's PM range is known to the flag decoration (the scan provides char offsets mapped to PM
  positions — see how claim flags are built + the offset→PM map). Replace that span with the approved
  phrasing using the SAME single-span replace the rewrite-span Accept uses (`tr.replaceWith(from, to,
  schema.text(...))`), then autosave (lossless — it's a normal edit, undoable). Do NOT use
  `replaceEditorContent` (that replaces the whole doc).
- Each nearest item should look clickable (hover state) and carry the phrasing (e.g. `data-phrasing`).
  Reuse the existing claim-popover wiring pattern (Approve/Edit/Disregard handlers).

## 2. @mention autocomplete (FE, composer-v5.js) + a teammates endpoint (backend)
In the comment composer's text field, when the user types `@`, show a **dropdown of logged-in teammates** to
pick from; selecting one inserts the mention and records the user reference in the comment's `mentions`.
- **Backend:** add a small endpoint to list teammates for autocomplete — `GET /api/users` (or
  `/api/me/teammates`) returning the verified users from the identity `users` table (id, name, email). Scope
  it behind the normal auth. (The users table is the Track-A identity directory.)
- **FE:** as the user types `@partial`, filter the teammate list and show a small dropdown anchored to the
  caret; arrow keys + Enter / click to select; insert `@Name` and add the user to the stored mentions. Keep
  it lightweight; reuse existing comment-composer plumbing. Don't fork the comment create call.

## 3. Slack DM the mentioned person (backend)
When a comment containing @mentions is created (the comments create route), **DM each mentioned teammate in
Slack**: "<author> mentioned you on <draft title>: <comment excerpt>" + a link to the draft. Reuse the
existing Slack integration; map the mentioned user → Slack via **`users.lookupByEmail`** (NOT list+filter —
known pagination gotcha). Build the mention STORAGE + the DM trigger so that even if Slack DM can't send, the
comment + mentions still persist (DM failure must be non-fatal/logged, never block the comment).
- **Dependency to flag (don't block on it):** the Artemis Slack bot needs DM permission (`chat:write` +
  `im:write`). If it's not granted, build + verify the rest and document the exact Slack-app scope Jon must
  enable. The comment/mention storage must work regardless.

## Acceptance (verify the EFFECT — browser + live where possible)
- Click-to-replace: flag a claim, open the popover, click a Nearest-Approved card → the document text changes
  to that phrasing and the flag clears on re-scan. Lossless/undoable. Screenshot before/after.
- @mention: type `@` in a comment → teammate dropdown appears, filters as you type, selecting inserts the
  mention; the comment persists with the mention recorded.
- Slack DM: creating a comment that @mentions a teammate sends them a Slack DM (prove with a live send to a
  real test user, OR document the missing scope + show the mention persisted + the send attempt logged).
- The selection toolbar STILL works exactly as now (regression check — don't touch it). No console errors.
  `./scripts/check.sh` for touched Python (note PRE-EXISTING separately).

## Constraints
Lossless (claim-replace is a normal undoable edit; comments/mentions never lost; Slack DM non-fatal). Reuse
the existing claim popover, comment composer, rewrite-span span-replace, Slack integration, and identity
users table — don't fork. Org dependency rule on any new deps (none expected; no <7-day-old packages).
Isolated worktree + own test DB. **Do NOT touch the selection toolbar.** **Do NOT merge** — report branch +
SHA + worktree + the three proofs + the Slack scope note. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
