# REVIEW1 — Fix Writing Studio save + add draft deep-link URL

**Paste-into:** Codex.
**Recommended Codex model / effort:** `gpt-5.4` · reasoning effort `medium`. A real bug fix (broken
save path) + a small router/deep-link addition + tests. Contained, but it's the load-bearing
foundation for the whole review flow, so use the flagship.
**Target branch:** `worker/review1-ws-save-deeplink`
**Fires:** now. **No migration.** Touches `artemis/marketing/routes/writing_studio.py`,
`public/js/features/writing-studio.js`, `public/js/core/navigation.js`, `public/js/core/api.js`.
Does NOT touch `marketing-os.js` (that's REVIEW2) — safe to run before/parallel to it, but REVIEW2
depends on this landing first (it needs the deep-link URL).
**Authoritative finding:** the internal-review audit (2026-06-02). **Writing Studio editing is the
single editing surface for campaign drafts; today saving is broken.**
**LOC cap:** ~200.
**Priority:** HIGH — editing-and-saving a draft in WS does not work right now.

---

## Why this exists
Drafts (`campaign_deliverables`) are listed + open in Writing Studio, and the body is editable, but
**saving is broken**: the frontend "Save Version" action (`writing-studio.js` `writing-save-version`,
~line 538-568) calls `createWritingDraftVersionApi(draftId, …)` → `POST /api/writing-studio/drafts/
{id}/versions` **which does not exist** (the router has no such route) → `_readJsonOrThrow` throws →
the `catch` aborts **before** the working `PUT /api/writing-studio/drafts/{id}` runs. So edits never
persist. Several sibling calls hit the same wall (`/regenerate`, `/edit-history`,
`/training-candidates` — no routes).

Separately, the WS draft deep-link is a **consume-once `localStorage` handoff**
(`marketing-os.js:_navigateToWritingStudio`), with **no URL param** — so a draft can't be linked
from Slack or bookmarked, and a reload loses the selection. REVIEW2 (Slack edit-link, campaign-page
edit-link) needs a real URL.

## Scope

### Part A — Make Save actually persist (the load-bearing fix)
- The working backend path already exists: `PUT /api/writing-studio/drafts/{id}` with `content`
  appends a new version to `deliverable_metadata.versions` and commits (`writing_studio.py:202-264`).
  **It is lossless (append-only) — keep it that way.**
- Fix the frontend Save: make `writing-save-version` persist via the working `PUT` path. Simplest
  correct fix (audit's recommendation): **drop the redundant `createWritingDraftVersionApi`
  (`/versions`) call** and rely on `updateWritingDraftApi` (PUT) — which already creates a version
  server-side. Verify the PUT path round-trips: edit body → Save → reload → edited content +
  a new version present in `deliverable_metadata.versions`.
- **Dead-route callers:** for `/regenerate`, `/edit-history`, `/training-candidates` — either remove
  the calls / hide the controls, OR add minimal real routes. Pick the smallest change that means
  **no WS control throws a 404 against a missing route.** Don't gold-plate; the bar is "every button
  in the WS draft view either works or isn't shown." Note what you did per control.
- Keep the `submit-review` + `events/{kind}` routes (they exist) working.

### Part B — Real draft deep-link URL
- Add a URL/hash param for a specific draft, e.g. `#writing-studio?draft=<deliverable_id>`, parsed in
  `navigation.js` (the shell router) and passed through to `loadWritingStudio({selectedDraftId})`.
- Keep the existing `localStorage` handoff working as a fallback, BUT the URL should win when
  present, and the selection must survive a reload (not consume-once when it comes from the URL).
- Export a tiny helper (or document the exact format) so REVIEW2 can build links:
  `writingStudioDraftHref(deliverableId) → "#writing-studio?draft=<id>"`. This is the contract
  REVIEW2 (Slack DM + edit buttons) builds on.

### Part C — Tests
- Backend: `PUT /drafts/{id}` with new content → 200, persists to `deliverable_metadata`, appends a
  version (lossless — prior version retained). (Extend the existing writing_studio route test.)
- Frontend: `node --check` on the touched JS. If there's a JS test harness for navigation parsing,
  add a case that `#writing-studio?draft=123` resolves `selectedDraftId=123`.

## Files owned
- EDIT: `artemis/marketing/routes/writing_studio.py` (only if adding minimal routes for dead callers)
- EDIT: `public/js/features/writing-studio.js` (fix Save; honor URL deep-link)
- EDIT: `public/js/core/navigation.js` (parse `?draft=` param)
- EDIT: `public/js/core/api.js` (remove/fix the missing-route wrappers)
- EDIT/NEW: a writing_studio route test

## Acceptance criteria
1. **Save round-trip (the fix):** with the app running, open a campaign draft in WS, edit the body,
   Save → **paste DB proof** that `campaign_deliverables.deliverable_metadata` got the new content +
   an appended version, AND no 404 in the network log. **Paste console (no errors) + DB before/after.**
2. **Deep-link:** loading `/#writing-studio?draft=<id>` opens that specific draft; survives reload.
   **Paste a description + console.**
3. No WS draft-view control throws a missing-route 404 (Part A dead-callers handled). **List what you
   did per control.**
4. `uv run pytest <writing_studio route test>` + `node --check` on touched JS. **Paste.**
5. `./scripts/check.sh` (j5b Jira flake exempt). **Paste summary.**
6. **COMMIT on `worker/review1-ws-save-deeplink`. Local git only.** Message ends
   `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## Hard constraints
- **Lossless** — saves APPEND versions, never overwrite/delete. No hard-delete.
- **Single editing surface** — WS is where editing happens; don't add edit-save elsewhere.
- **No new deps** (org rule). **Local-only git.**
- Don't touch `marketing-os.js` (REVIEW2 owns it) to avoid a merge collision.

## Report-back format
```
REVIEW1 — WS save + deep-link report
1. Commit / branch
2. The Save fix: what was calling the missing route, what you changed
3. Per dead-route control (/regenerate, /edit-history, /training-candidates): fixed-route vs hidden
4. Deep-link: the URL format + how navigation parses it + the helper REVIEW2 should use
5. Save round-trip proof (DB before/after + no 404)
6. Tests + check.sh
7. Surprises
```
