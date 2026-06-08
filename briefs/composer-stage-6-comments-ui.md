# Build brief — Composer Stage 6 FE: floating margin comments (Google-Docs style)

**Agent:** terminal (composer FE — owns `composer-v5.js`; design-coupled, Lead will eyeball the float feel).
**Branch:** `worker/composer-comments-ui` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_commentsui`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`,
`docs/COMPOSER-REBUILD-PLAN.md` (Stage 6), `docs/mockups/composer-v5-prototype.html` (the approved floating-
comment look), and the current `composer-v5.js`. **Codex is on the gdoc backend in parallel (new files) — no
overlap; you own composer-v5.js.**

## The point
Wire the merged comments backend into the composer as **floating Google-Docs-style margin comments**: select
text → comment; comments anchor to their span and float in the right margin (the rail that Stage 1's toggle
shows/hides); reply, resolve/reopen, @mention. Identity (Track A) is live → comments are attributed to the
logged-in user.

## Backend (MERGED — reuse, don't rebuild)
- `GET /api/writing-studio/drafts/{id}/comments` → comments (with `author{email,name}`, `replies[]`,
  `anchorStart/anchorEnd/anchoredText`, `status`, `mentions[]`, `resolvedBy`).
- `POST …/drafts/{id}/comments` `{body, anchorStart, anchorEnd, anchoredText, parentId?, mentions?}` →
  author = current user (server-set). `POST …/comments/{id}/resolve` · `/reopen` · `PATCH …/comments/{id}`.
- Current user: `GET /api/me` (Track A).

## Deliverables (all in composer-v5.js / .css — reuse existing infra)
1. **Render comments in the right margin**, anchored to their span. **Reuse the Stage-4 offset→PM-position
   map** (`serializeDocToTextWithMap`) to turn `anchorStart/anchorEnd` (char offsets) into PM positions →
   highlight the anchored span (a PM decoration) + float a comment card in the margin aligned to it, with a
   connector. Match the mockup: cards **expand/collapse** (collapsed = compact chip), and the existing
   **💬 Comments rail toggle** (Stage 1) shows/hides the whole margin. Re-anchor best-effort via
   `anchoredText` if offsets drift.
2. **Create a comment:** add a **"Comment"** action to the Stage-2 selection toolbar (alongside Rewrite/etc.)
   → select text → click → a small composer anchored to the selection → `POST` with the selection's
   anchorStart/anchorEnd/anchoredText. Author shows as the current user (`/api/me`).
3. **Reply / resolve / reopen:** reply box on a comment (`parentId`); Resolve/Reopen buttons → the endpoints;
   resolved comments collapse/dim but remain (lossless).
4. **@mention (v1, keep simple):** typing `@` in a comment offers a basic mention (free-text email or a
   simple list) → include in `mentions`. Show `@email` styled in the rendered comment + a "🔔 notified" hint.
   (Actual notification DELIVERY is out — backend just stores mentions.)

## Acceptance (verify the EFFECT — browser; Lead will eyeball the feel too)
- Select text → Comment → a comment is created (author = logged-in user) and appears **floating in the
  margin, anchored to that exact span** (underline/highlight on the right words — reuse the Stage-4 map so it
  lands precisely, incl. multi-paragraph). Prove the anchor lands on the right text.
- Reply nests under the comment; Resolve collapses/dims it (still present); Reopen restores. The 💬 rail
  toggle hides/shows all comments.
- @mention shows styled + is saved (GET shows it in `mentions`).
- No console errors; claim flags + pagination + picker + autosave all still work. `./scripts/check.sh` (note
  PRE-EXISTING failures separately). Browser-eyeball + screenshots for Lead.

## OUT OF SCOPE
Notification/ping DELIVERY (stored only); presence / live multi-cursor co-editing (deferred per roadmap);
Google Doc (Stage 7 FE — separate); the comments backend (merged). Don't touch backend/gdoc files.

## Constraints
Lossless (resolve = status; never delete). Author always from the server (current user) — FE just displays.
Reuse the Stage-4 offset→PM map for anchoring (don't reinvent), the Stage-2 selection toolbar, the Stage-1
rail toggle, and the prototype's comment look. Likely no migration. Isolated worktree + own test DB. **Do NOT
merge** — report branch + SHA + worktree + browser smoke (create→anchor-lands→reply→resolve→reopen→rail-
toggle) + screenshots. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews
(feel + code) + verifies + merges.
