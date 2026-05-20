# M7 — Writing Studio overview aggregator + draft CRUD

**Owner:** Worker (Sonnet). Background, isolated worktree off `lead/j6a-granola-integration` HEAD. Single Worker branch, no self-merge.
**Scope cap:** 500 LOC (matches the L estimate from the grounding doc).
**Depends on:** Existing C4 stub routes (`artemis/marketing/routes/writing_studio.py`) and existing `artemis/routes/writing_rules.py`. No new tables, no new migrations.
**Blocks:** Every later Writing Studio brief (M7b Google Docs, autosync, regenerate, versions, edit history, training candidates). Those briefs all assume the page loads.

> All file paths in this brief are relative to the repo root. The harness controls the worktree. Do not invent absolute paths from your session context.

## Why

The Writing Studio frontend is fully ported from the Node era — `public/js/features/writing-studio.js` is ~2k LOC of folder browser, draft library, compose UI, version panel, sync drawer, rules/examples/sources surface — and it hard-fails on first load because its very first call (`GET /api/writing-studio/overview`) returns 404 against the Python backend.

The audit report (`audits/marketing-gap-report-v2.md`, "2. Writing Studio") makes this the single largest user-visible gap in Marketing. The C4 bridge that exists (`POST /drafts` from candidate, `POST /drafts/{id}/submit-review`, `POST /drafts/{id}/events/{kind}`) is useful integration plumbing for the campaign workspace, but does not load the application.

This brief closes that one gap and only that one gap. The goal in a sentence: **the Writing Studio page loads with real data instead of a "Could not load Writing Studio" error**, even if every aggregator key is an empty array.

Everything else the Node-era frontend can do — Google Docs import/export/unlink, autosync export/import/inspect/reconcile, versions, regenerate, edit history, training candidates, seed import — stays broken on purpose and gets its own brief later. Resist scope creep.

## What's in scope

The aggregator endpoint and the small set of draft CRUD routes that the existing frontend calls during a normal browse/edit session. Read what `loadWritingStudio()` actually depends on (lines ~107–179 of `public/js/features/writing-studio.js`) and build only those shapes.

### Likely route surface (verify against `public/js/core/api.js` lines ~640–810)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/writing-studio/overview` | Aggregator. Returns `{ drafts, folders, campaigns, rules, examples, sources, profiles, training_candidates, sync_config }`. |
| GET | `/api/writing-studio/drafts` | Paginated list. Query params likely include `folderId`, `campaignId`, `status`, `cursor`, `limit`. |
| GET | `/api/writing-studio/drafts/{draft_id}` | Detail. Frontend reads `title`, `status`, `asset_type`, `folder_id`, `folder_name`, `campaign_id`, `threadMessages`, `metadata` (versions inside `metadata`). |
| PATCH | `/api/writing-studio/drafts/{draft_id}` | Update — title, status, content, folder_id. Frontend uses for rename + folder move + content save. |
| DELETE | `/api/writing-studio/drafts/{draft_id}` | Soft-archive (set `status = 'archived'` in metadata or status column — match what frontend expects). |

These are the minimum to make the page render, allow folder browsing, allow opening a draft, and allow renaming + deleting. Anything not in this table — `/compose`, `/prompt`, `/invoke`, `/google-doc/*`, `/sync/*`, `/regenerate`, `/edit-history`, `/versions`, `/training-candidates/*`, `/seed/import`, `/rules`, `/examples`, `/sources` mutation routes — is **explicitly out of scope** for M7.

### Data sourcing

No new tables. Aggregate from what exists:

- **drafts** — `campaign_deliverables` (see `artemis/marketing/models.py` line 274). Each row's `deliverable_metadata` JSONB carries `title`, `asset_type`, `folder_id`, `versions[]`, etc. that the C4 invoke path already writes. Read `artemis/marketing/writing_studio/invoke.py` to confirm the metadata shape the existing `POST /drafts` writes, and match the read serializer to it.
- **folders, rules, examples, sources, profiles** — `artemis/writing_rules/models.py` already defines `WritingFolder`, `WritingRule`, `WritingExample`, `WritingSource`, `WritingProfile`. The routes live at `/api/writing-rules/*` (`artemis/routes/writing_rules.py`). The aggregator should query their repositories directly (read-only) rather than HTTP-self-calling. Import `artemis.writing_rules.repository` and reuse.
- **campaigns** — `campaign_candidates` (already exposed via existing campaigns route). Pull the minimal subset (`id`, `name`/`title`, `status`) the frontend needs to populate the campaign filter dropdown.
- **training_candidates** — if there's no existing table or store, return `[]`. Do not invent storage.
- **sync_config** — if no existing config store, return `{}` or a minimal default `{ rootDir: null, machineLabel: null, autoSync: false }`. Frontend already merges with `localStorage` (`artemis-writing-studio-sync` key) so an empty server payload is fine.

### Fallback contract

For any aggregator key whose backing data simply doesn't exist yet (training_candidates is the canonical example), **return an empty array or empty object, never a 500**. The aggregator must succeed even on a fresh DB.

### Frontend expectations to honor

From a fast read of `loadWritingStudio()` and the row renderers (lines ~1020–1240 of `public/js/features/writing-studio.js`):

- Each `draft` row needs: `id`, `title`, `status`, `asset_type`, `campaign_id`, `folder_id`, `folder_name`, `updated_at`, and a `metadata` blob.
- The detail response (`GET /drafts/{id}`) additionally needs: `threadMessages` (array — return `[]` if not stored), `content` (latest version's body — pull from `metadata.versions[-1].body` or wherever invoke.py wrote it), and the full `metadata` blob (frontend parses `metadata.review`, `metadata.versions`, etc.).
- `folder` rows need: `id`, `name`, `parent_id`, and the frontend computes counts client-side from the drafts array. The aggregator does NOT need to precompute `draft_count`.

Read the frontend before committing to a shape. The brief's example shape is a starting point, not a contract.

## What's explicitly out of scope

- Google Docs import / export / unlink — separate brief (M7b).
- Autosync export/import/inspect/reconcile (`/api/writing-studio/sync/*`) — separate brief.
- Compose / prompt / invoke / regenerate routes — separate brief.
- Versions list/create endpoints (`/drafts/{id}/versions`) — separate brief.
- Edit history (`/drafts/{id}/edit-history`) — separate brief.
- Training candidates pipeline (`/training-candidates`, `/training-candidates/{id}/decision`) — separate brief.
- Seed import (`/seed/import`) — separate brief.
- Rules / examples / sources **mutation** through `/api/writing-studio/*` — the existing `/api/writing-rules/*` routes already cover this; the frontend can be wired to those in a later polish brief. M7 only needs the **read** surface for the overview.
- Defining the Writing Studio agent itself — per Q5 (resolved 2026-05-20), Writing Studio is ONE agent and its definition lives in M5. M7 is the read surface for what already exists; it does NOT instantiate or invoke any agent.

## Hard constraints

- Total scope ≤ **500 LOC**. Use `git diff --staged | grep -c '^+'` for honest self-reporting; do not estimate.
- Single commit on the Worker branch. Commit message: `feat(m7): writing_studio overview aggregator + draft CRUD routes`.
- Before the commit: run `pwd && git branch --show-current` AND `git diff --staged | head -200`. The CWD-trap is documented in `briefs/CONVENTIONS.md` — re-read it if you've been spawned more than 5 minutes ago.
- Background execution. Isolated worktree off `lead/j6a-granola-integration` HEAD. Worker does NOT merge to lead.
- No new tables. No new Alembic migrations. If you find yourself wanting one, stop and re-read scope — you're rebuilding the studio, not loading it.
- No regression on the three existing stub routes (`POST /drafts`, `POST /drafts/{id}/submit-review`, `POST /drafts/{id}/events/{kind}`). Run the existing `artemis/marketing/tests/` suite green before commit.
- Per-route tests: at least one happy path + one failure mode each (auth missing, draft not found, malformed body — pick the relevant one per route).
- `ruff check` + `mypy` clean on changed files.

## Acceptance checklist

- [ ] Open Writing Studio in the browser after merge. The page loads. No "Could not load Writing Studio" error banner.
- [ ] Empty-DB case verified: with zero drafts/folders/rules, the overview still returns 200 with `{ drafts: [], folders: [], campaigns: [], rules: [], examples: [], sources: [], profiles: [], training_candidates: [], sync_config: {} }` (or equivalent empty shape).
- [ ] Create one draft via the existing C4 flow: `POST /api/writing-studio/drafts` with a real `candidate_id`. The new draft appears in `GET /api/writing-studio/overview` under `drafts[]` AND in `GET /api/writing-studio/drafts`.
- [ ] `GET /api/writing-studio/drafts/{id}` returns the same draft with full metadata (versions, status, content).
- [ ] `PATCH /api/writing-studio/drafts/{id}` with `{ "title": "Renamed" }` updates the title; subsequent `GET` reflects it; browser refresh shows the new title in the left rail.
- [ ] `DELETE /api/writing-studio/drafts/{id}` soft-archives (does NOT hard-delete the row). Subsequent overview no longer shows it in the default draft list, but the row still exists in DB.
- [ ] `loadWritingStudio()` → `fetchWritingStudioOverview()` returns 200, not 404. Captured via DevTools network tab in the smoke screenshot.
- [ ] Existing C4 routes still pass their tests: `POST /drafts`, `POST /drafts/{id}/submit-review`, `POST /drafts/{id}/events/{kind}`. Run `pytest artemis/marketing/tests/` and paste the green summary line.
- [ ] New tests added per route, happy + one failure mode each.
- [ ] `ruff check` and `mypy` clean on changed files.
- [ ] `git diff --staged` re-read twice before commit (twice-bitten rule).
- [ ] `pwd && git branch --show-current` evidence pasted in the report.
- [ ] LOC count via `git diff --staged | grep -c '^+'` pasted in the report.

## Where to start

1. Read this brief twice.
2. Read `briefs/CONVENTIONS.md` ("CWD trap" + path conventions) — non-optional.
3. Read `public/js/features/writing-studio.js` lines 107–180 (the loader) and lines 1020–1240 (the row renderers) to confirm the exact field names the frontend reads.
4. Read `public/js/core/api.js` lines 639–810 to confirm the exact route paths and HTTP shapes.
5. Read `artemis/marketing/writing_studio/invoke.py` to confirm what `metadata` the existing `POST /drafts` writes — your serializer reads this back.
6. Read `artemis/routes/writing_rules.py` + `artemis/writing_rules/repository.py` — reuse this repository layer from inside the aggregator rather than HTTP-self-calling.
7. Add the new routes to `artemis/marketing/routes/writing_studio.py`. Match the existing file's auth dependency (`require_token`) and error helper style.
8. Run the existing test suite — it must stay green. Then add the new tests.
9. Manual smoke: spin up the app, open Writing Studio, screenshot the page loaded + DevTools network tab showing `/api/writing-studio/overview` returning 200.
10. Run the acceptance checklist top-to-bottom. Each box is either green with verbatim evidence, or explained with why not.
11. Report.

## Paste-ready Worker prompt (for terminal-Lead to spawn)

```
Implement briefs/m7-writing-studio-overview.md.

Scope: ≤ 500 LOC. Background, isolated worktree off
lead/j6a-granola-integration HEAD. Single Worker branch — do NOT
merge to lead.

CRITICAL framing:
- Read briefs/CONVENTIONS.md first ("CWD trap" + path conventions).
- This is the read surface only. Anything that mutates beyond
  draft CRUD (compose, sync, versions, regenerate, training,
  google docs) is out of scope. Resist scope creep.
- Writing Studio is ONE agent per Q5 (resolved 2026-05-20). The
  agent definition lives in M5, not in this brief.
- No new tables, no new migrations. Aggregate from existing
  campaign_deliverables, writing_rules, writing_folders,
  writing_examples, writing_sources, writing_profiles. Empty
  arrays/objects for keys without backing data.
- Frontend defines the contract. Read public/js/features/
  writing-studio.js lines 107–180 + 1020–1240 and public/js/core/
  api.js lines 639–810 BEFORE deciding response shapes.
- Existing C4 stub routes (POST /drafts, /submit-review, /events)
  must stay passing.

Reflexes before EVERY commit:
- pwd && git branch --show-current
- git diff --staged | head -200 (re-read twice)
- LOC self-report via `git diff --staged | grep -c '^+'` — no
  estimates

Commit message: feat(m7): writing_studio overview aggregator +
draft CRUD routes

Report when complete: branch SHA, full-diff LOC count, every
acceptance bullet either green with verbatim evidence or
explained, a curl smoke against /api/writing-studio/overview
(headers + 200), and a screenshot of the Writing Studio page
loading in the browser with the network tab open.
```
