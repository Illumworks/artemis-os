# Build brief — Composer Stage 3: the full drafts picker (header popover + single "+" menu)

**Agent:** terminal (design-coupled — match the mockup; Lead reviews the feel). **Branch:**
`worker/composer-drafts-picker` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_picker`). **Do NOT merge — report.** Read first: `docs/AGENT-WORKING-PRINCIPLES.md`,
`docs/COMPOSER-REBUILD-PLAN.md` (Stage 3), `docs/mockups/composer-v5-prototype.html` (the approved picker
look), and Stage-1's `public/js/features/composer-v5.js` (the basic picker hook you extend).

## The point
Finish the drafts picker into the **Finder-style header popover** from the v5 mockup: a folder tree + the
drafts inside, popping from the header button (no descriptive label), with a **single "+" create menu**
(New draft · New from template · New folder). Stage 1 shipped a *basic* picker popover — this completes it.

## Build on what exists (don't fork)
- **Picker hook (Stage 1):** `composer-v5.js` already opens a drafts popover from the header
  (`data-cv5="drafts-btn"` / `drafts-picker`). Upgrade it in place.
- **Backends (all merged — reuse, don't rebuild):**
  - Drafts + folders: `GET /api/writing-studio/overview` (drafts, folders), folder CRUD (`POST/PUT/DELETE
    /api/writing-studio/folders`), draft create (`POST /api/writing-studio/drafts`).
  - **Templates (Stage-8 backend, merged):** `GET /api/writing-studio/templates?status=active` (list) +
    `POST /api/writing-studio/templates/{id}/apply` (instantiates a NEW draft from a template, returns its
    id). This is what "New from template" calls.
- **Open-a-draft:** the composer already loads a selected draft (`onSelectDraft` callback in the Stage-1
  mount). Clicking a file in the picker selects + opens it.

## Deliverables
1. **Finder-style picker popover** from the header button: a **folder tree** (nested folders, expand/
   collapse) with the drafts inside each, + an "all/ungrouped" view. Click a draft → it opens in the
   composer (reuse `onSelectDraft`). Match the mockup's look (clean, no caption text). Close on outside-click/
   escape.
2. **Single "+" create menu** (a small popup from one "+" in the picker — NOT three inline buttons, per
   Jon):
   - **New draft** → create a blank draft (existing create path) → open it.
   - **New from template** → show the active templates (`GET …/templates`), pick one → `POST …/templates/{id}/
     apply` → open the returned new draft (its body pre-filled from the template). If there are no templates,
     the item can be disabled/hidden.
   - **New folder** → create a folder (existing folder CRUD), refresh the tree.
3. Keep everything else in the composer intact (the editor, autosave, claim flags, comments-rail toggle).

## Acceptance (verify the EFFECT — show it)
- Open the picker from the header → folder tree + drafts render; clicking a draft opens it in the composer.
- "+" → **New draft** creates + opens a blank draft.
- "+" → **New from template** lists templates, applying one opens a new draft whose body = the template
  (prove the body carried in via a fresh GET of the new draft).
- "+" → **New folder** creates a folder; it appears in the tree; a draft can be filed under it.
- No console errors; matches the mockup's look. `./scripts/check.sh` for any touched Python (note
  PRE-EXISTING failures separately). Browser-eyeball the picker layout (Lead will too).

## OUT OF SCOPE
Pagination (Stage 5), comments (Stage 6 — only the rail toggle exists), Google Doc (Stage 7), the ⋯ Actions
menu (Stage 8). Don't touch the claim-flag or identity code.

## Constraints
Lossless (folder delete is tombstone/soft per existing CRUD; never lose drafts). Reuse the existing folder/
draft/template backends — do NOT add endpoints (the templates apply endpoint already exists). Match the
approved mockup. Likely no migration. Isolated worktree + own test DB. **Do NOT merge** — report branch +
SHA + worktree + a browser smoke of: open picker → open a draft → New draft → New from template (body
carried) → New folder. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews
(feel + code) + verifies + merges.
