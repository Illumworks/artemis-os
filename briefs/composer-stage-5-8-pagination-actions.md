# Build brief — Composer Stages 5 + 8 (format-aware pagination + ⋯ Actions menu)

**Agent:** terminal (composer FE — owns `composer-v5.js`; bundle 5+8 in ONE branch to avoid self-conflict).
**Branch:** `worker/composer-pagination-actions` off `main`. **Own git worktree, cwd inside. Own test DB**
(`artemis_test_pgact`). **Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md`,
`docs/COMPOSER-REBUILD-PLAN.md` (Stages 5, 8), `docs/mockups/composer-v5-prototype.html`, and the current
`public/js/features/composer-v5.js`. **Codex is building the comments backend in parallel — you stay in the
composer FE; no overlap.**

## Stage 5 — format-aware pagination (pure FE)
In the document pane, render **format-aware** page layout based on the draft's asset type:
- **Long-form** types (guide, paper, whitepaper, long-form, field guide) → break the document into visual
  **pages (Page 1 · 2 · 3 …)** as content flows past a page height.
- **Email / short** types → **one continuous page** (no breaks). This is the default + must look unchanged
  from today for emails.
- Drive it off the draft's `asset_type` (and/or length). Keep it visual only — does NOT change stored
  content or the autosave/serialization (the doc model is unchanged; pagination is presentation). Per the v5
  mockup: "pagination just happens for long-form types" — no caption/label.

## Stage 8 — the ⋯ Actions menu (header)
Add the **⋯ Actions** menu to the slim header (it's a placeholder today). Items:
- **Save as template** → take the current draft's body + create a template via the templates backend
  (`POST /api/writing-studio/templates` — merged; body = current doc text, name = prompt or draft title).
  Confirm it round-trips (GET templates shows it; it then appears under the picker's "New from template").
- **Repurpose** and **Brand + readability check** → render as menu items but they can be **stubs** for now
  (a "coming soon" toast / no-op) — wiring them is later; the menu shell + Save-as-template is the deliverable.
- (New-from-template already lives in the Stage-3 picker "+" menu — don't duplicate; the Actions menu is for
  save-as / repurpose / check.)

## Build on what exists (don't fork)
- `composer-v5.js` — the editor, header, picker, autosave, claim flags. Extend in place; don't regress them.
- Templates backend (merged): `listWritingTemplatesApi` / `applyWritingTemplateApi` in `api.js`; add a
  create-template call for Save-as-template (`POST /api/writing-studio/templates`).
- Match the mockup's look for the ⋯ menu (small popover, clean).

## Acceptance (verify the EFFECT — browser smoke)
- A long-form draft (asset type guide/paper) → renders in pages; an **email draft → stays one continuous
  page** (prove the email case is unchanged — that's the one that must not regress).
- ⋯ → **Save as template** on a draft → a template is created (fresh `GET /templates` shows it) and it
  appears in the picker's New-from-template list.
- ⋯ → Repurpose / Brand check show as items (stubs OK).
- No console errors; claim flags + autosave + picker still work. `./scripts/check.sh` for any touched Python
  (none expected — note PRE-EXISTING failures separately). Browser-eyeball; Lead will too.

## OUT OF SCOPE
Comments (Stage 6 — terminal does the FE next, after Codex's backend lands); Google Doc (Stage 7 — needs
Track B); functional Repurpose/Brand-check (stubs now). Don't touch the comments backend files.

## Constraints
Lossless (pagination is presentation-only — never alters stored content; Save-as-template is additive).
Reuse the templates backend + existing editor; don't fork. Bundle 5+8 in one branch (both are composer-v5.js
— avoids conflicts). Likely no migration. Isolated worktree + own test DB. **Do NOT merge** — report branch +
SHA + worktree + browser smoke (long-form pages vs email continuous; Save-as-template round-trip). Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews (feel + code) + verifies + merges.
