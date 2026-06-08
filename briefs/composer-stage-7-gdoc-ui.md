# Build brief — Composer Stage 7 FE: Google Doc header UI (connect · import · export) — THE LAST COMPOSER PIECE

**Agent:** terminal (composer FE — owns `composer-v5.js`). **Branch:** `worker/composer-gdoc-ui` off `main`.
**Own git worktree, cwd inside. Own test DB** (`artemis_test_gdocui`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md`, `docs/COMPOSER-REBUILD-PLAN.md` (Stage 7), `docs/mockups/composer-v5-
prototype.html` (the header "⊞ Google Doc" affordance), and the current `composer-v5.js`. This completes the
v5 composer.

## The point
Wire the merged gdoc backend into the composer's header "⊞ Google Doc" affordance: connect a Google account
(per-user), import a Google Doc into the draft, export the draft to a Google Doc.

## Backend (MERGED — reuse, don't rebuild)
- `GET /api/google/status` → `{connected: bool, email?}` (current user).
- `GET /api/google/oauth/start` → 302 to Google consent (callback completes the connect server-side).
- `POST /api/google/disconnect`.
- `POST /api/writing-studio/drafts/{id}/google-doc/import` `{docUrl}` → imports the Doc's text into the draft
  (via live_content) + links the doc; returns the imported content. **409 `google_not_connected`** if the
  user hasn't connected.
- `POST /api/writing-studio/drafts/{id}/google-doc/export` → creates a Doc from the draft, returns
  `{url}`, links it in draft metadata.
- Add the API helpers in `api.js` (googleStatus / googleDisconnect / importGoogleDoc / exportGoogleDoc).

## Deliverables (header affordance in composer-v5.js / .css)
1. **Connect state:** on composer load, call `GET /api/google/status`. The header "⊞ Google Doc" control
   reflects it: **not connected** → "Connect Google" (clicking opens `/api/google/oauth/start` — a popup or
   same-tab redirect; on return, re-check status). **Connected** → show the account email + an import/export
   menu, with a "Disconnect" option.
2. **Import:** a small input/prompt for a Google Doc URL → `POST …/google-doc/import` → on success, the
   imported text loads into the editor (reload the draft / set the editor content) and the doc is linked.
   Show a clear state if the user isn't connected (the 409 → prompt to Connect first).
3. **Export:** "Export to Google Doc" → `POST …/google-doc/export` → show/open the returned Doc URL
   (e.g. a toast/link "Opened in Google Docs"). If a doc is already linked, it updates/links it.
4. Match the mockup's compact header treatment ("⊞ Google Doc · link · export") — clean, contextual, no
   clutter. Keep editor/comments/claim-flags/picker/autosave intact.

## Acceptance (verify the EFFECT)
- Status reflects connected/not-connected (mock `/api/google/status` in a browser test, or eyeball against
  the real backend which returns `{connected:false}` for a fresh user).
- Not-connected → import shows "connect first" (handles the 409), Connect opens the consent flow.
- With a MOCKED connected state + mocked import/export (or note that a true live connect needs the operator
  to authorize in-browser), prove the import loads text into the editor and export surfaces a Doc URL.
- A LIVE end-to-end (real Google) is the final demo check — only doable once the operator clicks Connect in
  the real app (app.artemisos.me); note it, don't gate the build on it.
- No console errors; `./scripts/check.sh` for any touched Python (none expected — note PRE-EXISTING
  failures). Browser-eyeball + screenshots for Lead.

## OUT OF SCOPE
The gdoc backend (merged); per-doc Drive picker UI (URL paste is fine for v1); notification of imports.

## Constraints
Lossless (import goes through the normal draft content path — never destroys versions). Reuse the merged
gdoc backend + the existing header; don't fork. Likely no migration. Isolated worktree + own test DB. **Do
NOT merge** — report branch + SHA + worktree + browser smoke (status reflects state; connect opens consent;
import-not-connected→prompt; mocked import-loads-text + export-returns-url). Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges. **After
this merges, the v5 composer is feature-complete.**
