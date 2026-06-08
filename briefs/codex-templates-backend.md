# Codex brief — Templates backend (structured templates + CRUD + seed + apply)

**Agent:** Codex. **Branch:** `worker/templates-backend` off `main`. **Own git worktree, cwd inside. Own
test DB** (`artemis_test_templates`: createdb + `CREATE EXTENSION vector` + `ARTEMIS_DB_URL=...artemis_test_
templates uv run alembic upgrade head` + export `ARTEMIS_TEST_DB_URL`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` (root-cause; verify the EFFECT live). **Runs in PARALLEL with terminal's
Stage-2 composer work — touch only the files below; do NOT touch `public/js/features/composer-v5.js`,
`writing-studio.js`, `public/vendor/`, or `artemis/marketing/routes/writing_studio.py`.** This MIRRORS the
just-merged Claims Register slice (`briefs/codex-claims-register-backend.md` + `artemis/marketing/routes/
claims.py`) — build it the same way.

## The point of this slice
The composer's "Save as template / New from template" feature needs real, structured templates. Today they
exist only as markdown in the seed corpus (`07_TEMPLATES`). This slice makes them queryable data: a table,
CRUD, a seed from the corpus, and an **apply** endpoint that instantiates a template into a new draft. **Data
+ CRUD + seed + apply ONLY** — the composer UI for it is a later stage.

## Mirror these patterns (Claims Register did exactly this — copy its shape)
- Model: add to `artemis/writing_rules/models.py` (mirror the `Claim` model added there — same Base/column/
  constraint style, per-profile FK, `superseded_by`, status, timestamps).
- Repo: `artemis/writing_rules/repository.py` (mirror the claims repo functions).
- Schemas: `artemis/writing_rules/schemas.py` (mirror `ClaimRead` + create/update).
- Routes: NEW file `artemis/marketing/routes/templates.py` (mirror `claims.py`), register in `artemis/main.py`.
- Seed: `artemis/writing_rules/seed_corpus.py` — the `"07_TEMPLATES.md"` entry (~line 554) is the markdown
  to parse (templates are `## Template A — <name>` blocks with copy-ready body text).

## 1. Model + migration (additive, lossless — new revision off current head 0072)
New model `Template` (table `templates`): `id`; `profile_id` FK → writing_profiles; `template_key` (text,
e.g. "A"); `name` (text, NOT NULL); `asset_type` (text, nullable — e.g. email/one-liner); `body` (text, NOT
NULL — the copy-ready template, plain-text+markdown to match the composer's content format); `status` (text,
NOT NULL default `'active'`; allowed `active` · `retired`); `superseded_by` (self-FK, nullable, lossless);
`created_at`/`updated_at`. UniqueConstraint `(profile_id, template_key)`; CHECK on status. Migration chains
off **0072** (→ 0073). downgrade drops the table. No deletes anywhere.

## 2. Repo + 3. API (new router `/api/writing-studio/templates`)
- `list_templates(session, profile_id, status=None)`, `get_template`, `get_by_profile_key`, `create_template`,
  `update_template`, `retire_template` (status→retired, lossless — NOT delete).
- `GET /api/writing-studio/templates?profileId=&status=` · `GET /{id}` · `POST` (create) · `PATCH /{id}` ·
  `POST /{id}/retire`. Proper errors (404 not_found, 409 conflict on dup key) mirroring claims.py.
- **APPLY:** `POST /api/writing-studio/templates/{id}/apply` body `{title?, folderId?}` → creates a NEW draft
  (CampaignDeliverable) seeded with the template `body` as its initial content/version, returns the new
  draft's id (+ basic detail). Reuse the existing draft-creation path (see how `POST /drafts` /
  `create_handoff_draft` build a deliverable) — do NOT fork draft creation. This is the backend half of
  "New from template"; the composer wires the menu to it later.

## 4. Seed
`import_templates(session, profile_id)` (or extend the corpus import like claims did): parse the
`07_TEMPLATES` markdown into `Template` rows (`template_key` + `name` from `## Template X — name`, `body` =
the block's copy-ready text), `status='active'`, idempotent (upsert on `(profile_id, template_key)`).

## Acceptance (verify the EFFECT live — paste output)
- Migration up/down round-trips off 0072.
- Seed → `GET /templates` returns the parsed templates (paste a couple with name + body snippet). Idempotent
  (run twice → same count).
- `POST` create → `PATCH` edit → `POST /retire` → GET shows `retired` and it STILL EXISTS (lossless).
- `POST /{id}/apply` → returns a new draft id; `GET /drafts/{newId}` shows the template body as its content.
- Unit/integration tests (repo lifecycle + endpoints + seed parser + apply-creates-draft). `./scripts/
  check.sh` clean (note PRE-EXISTING failures separately — known ruff drift in unrelated files; list, don't
  fix).

## OUT OF SCOPE
The composer's template UI (Save-as / New-from menus — later stage); AI-generated templates; touching the
fenced composer files. Just the store + CRUD + seed + apply endpoint.

## Constraints
Lossless (no deletes; retire = status; `superseded_by`). Additive migration off head **0072** (the chain is
linear now — the composer foundation added no migration). New router file + main.py registration only (no
edits to writing_studio.py). Mirror the Claims Register patterns; don't fork draft creation. Org dep rule.
Isolated worktree + own test DB. **Do NOT merge** — report branch + final SHA + worktree path + the seeded-
templates output + the create→retire lifecycle + the apply→new-draft proof. Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
