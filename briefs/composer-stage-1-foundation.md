# Build brief — Composer Stage 1: the foundation (ProseMirror editable document + v5 shell)

**Agent:** terminal (novel + design-coupled — Lead prototyped + will review). **Branch:**
`worker/composer-foundation` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_composer`: createdb + `CREATE EXTENSION vector` + `ARTEMIS_DB_URL=...artemis_test_composer uv
run alembic upgrade head` + export `ARTEMIS_TEST_DB_URL`). **Do NOT merge — report.** Read first:
`docs/AGENT-WORKING-PRINCIPLES.md`, `docs/COMPOSER-REBUILD-PLAN.md` (the full plan + engine decision), and
**`docs/mockups/composer-v5-prototype.html`** (the Jon-approved visual + interaction spec — match it).

## The point of this stage
Today the Writing Studio composer is **chat-only**: the draft renders as a read-only bubble in the chat
thread; there is no editable document (the old `data-writing-field='draft-content'` textarea is dead). This
stage builds **the editable document half** to the v5 layout: **chat LEFT / live editable document RIGHT**,
inside the app shell. It's the foundation every later stage (selection→AI edit, claim flags, comments,
pagination) attaches to. **Foundation only — see OUT OF SCOPE.**

## Current code (build on it, don't fork)
- Composer FE: `public/js/features/writing-studio.js` (~3,383 lines, vanilla ES module; `renderWritingStudio()`
  ~929, `renderDraftCanvas()` ~1350, chat thread `renderWritingChatThread()`, compose via
  `composeWritingDraftApi` → `POST /api/writing-studio/drafts/{id}/compose`, draft update via
  `updateWritingDraftApi(id,{content})` → `PUT /api/writing-studio/drafts/{id}`). CSS:
  `public/css/features/writing-studio.css`.
- Draft load: `GET /api/writing-studio/drafts/{id}` (detail incl. content + thread + version history).
- All needed backend EXISTS — this is a front-end build plus autosave wiring; do not add backend endpoints.

## Deliverables (Stage 1)
1. **Vendor ProseMirror LOCALLY (no bundler, no runtime CDN).** Download pre-built **ESM** bundles of the
   pinned packages into `public/vendor/prosemirror/` and import them as ES modules from the app. Pin these
   stable (years-old) versions — the prototype validated them: `prosemirror-state@1.4.3`,
   `prosemirror-view@1.33.6`, `prosemirror-model@1.23.0`, `prosemirror-schema-basic@1.2.3`,
   `prosemirror-schema-list@1.4.1`, `prosemirror-example-setup@1.2.3`, `prosemirror-keymap@1.2.2`,
   `prosemirror-history@1.4.1`. **Must work offline / in prod (no esm.sh at runtime).** Commit the vendored
   files; record the versions in the brief/PR notes (org dep rule — all mature, fine; never adopt <7 days
   old).
2. **v5 layout inside the app shell.** Restructure the composer to chat LEFT (~38%) / document RIGHT (~62%)
   of the app content area (to the RIGHT of the persistent app nav rail — do NOT take the full viewport).
   Slim header per the mockup: drafts-picker button · title · status · N variants · N rules · **💬 Comments
   toggle** · History · ⊞ Google Doc · ⋯ Actions · Save version. Include the **comments-rail toggle** (hide/
   show the whole right comment margin; document reclaims width) — that interaction is in the mockup and is
   the one comment-related thing in scope this stage.
3. **The editable document = a real ProseMirror editor**, mounted in the right column, loaded with the draft's
   current content. Schema: paragraphs, headings, lists, bold/italic (the prototype's basic set). Genuinely
   editable (click + type). Replace the read-only chat-bubble rendering of the draft.
4. **Autosave (lossless).** Debounced (~1–1.5s after typing stops) persist of the document back via
   `PUT /drafts/{id}`. Must NOT break version history / Save-version (those stay). Show a small "Saving…/
   Saved" indicator. No silent overwrite of versions.
5. **Keep the LEFT chat working.** The existing compose chat (conversation thread + composer input "Ask Amira
   to draft, rewrite, or refine…", no quick-action chips) stays functional in the left column. At minimum
   preserve today's behavior; ideally a chat reply that produces/rewrites a draft updates the document.

## Key decision to make (flag your choice in the report — Lead will review)
**Persisted content format.** PM needs to load + serialize the draft body. Recommended: serialize the
document to **HTML** and store it in the existing draft `content` field, loading it back into PM on open —
BUT confirm this stays **lossless** and doesn't break consumers of `content` (the compose engine's draft
context, version history, any export). If HTML-in-`content` risks a consumer, prefer storing PM HTML while
keeping a plain-text projection, or another reversible scheme. Whatever you choose: existing drafts must load
correctly (plain text → paragraphs) and nothing loses content. State your decision + why.

## OUT OF SCOPE (later stages — do NOT build now)
Selection→AI-edit toolbar (Stage 2); functional claim flags (Stage 4 — no inline claim detection this stage);
functional comments (Stage 6 — only the rail TOGGLE + an empty margin placeholder, no comment CRUD/identity);
pagination (Stage 5); Google Doc import/export (Stage 7); ⋯ Actions functionality (Stage 8); tagging UI
(separate). Header affordances for these can render as static/disabled placeholders matching the mockup, but
wire NO behavior beyond the comments-rail toggle.

## Acceptance (verify the EFFECT live — don't stop at "it renders")
- Open a REAL draft that has content → it appears in an **editable ProseMirror document** in the right
  column, inside the app shell (nav rail visible, ~38/62 split, no horizontal overflow).
- Type an edit → after the debounce it autosaves; **GET the draft (fresh request) shows the edit persisted**;
  reload the page → the edit is still there. (Prove persistence with a fresh fetch, not just in-memory.)
- Version history / Save-version still works (lossless — show a version is preserved).
- The left chat still composes (existing behavior intact).
- The 💬 Comments toggle hides/shows the right margin and the document reclaims width.
- ProseMirror loads from the **local vendored** files (disable network and confirm it still works).
- `./scripts/check.sh` for any touched Python; note PRE-EXISTING failures separately (known ruff-format drift
  in unrelated files — list, don't fix). Frontend: no console errors on load/edit/save.

## Constraints
Lossless (never lose draft content or versions; AI/auto changes are saved, not destructive to history).
Reuse the existing compose engine + draft CRUD + header affordances; don't fork. Vendor PM locally (org dep
rule). Match the approved mockup's look/interactions. Isolated worktree + own test DB. **Do NOT merge** —
report branch + final SHA + worktree path + paste proof of the autosave round-trip (edit → fresh GET shows
it) + your persisted-format decision + confirmation PM loads from local vendored files offline. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews (look + code) + verifies +
merges.
