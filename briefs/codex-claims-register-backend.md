# Codex brief — Claims Register backend (structured "living bible" + CRUD + seed)

**Agent:** Codex. **Branch:** `worker/claims-register` off `main`. **Own git worktree, cwd inside. Own test
DB** (`artemis_test_claims`: createdb + `CREATE EXTENSION vector` + `ARTEMIS_DB_URL=...artemis_test_claims uv
run alembic upgrade head` + export `ARTEMIS_TEST_DB_URL`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` (root-cause; verify the EFFECT live). **Runs in PARALLEL with terminal's
composer build — touch only the files below; do NOT touch `public/js/features/writing-studio.js`,
`public/css/features/writing-studio.css`, `public/vendor/`, or `artemis/marketing/routes/writing_studio.py`.**

## The point of this slice
The composer's "Claim not in Register" flag (a later stage) needs a **structured list of approved claims**
to check against. Today the claims register exists ONLY as a markdown blob in the seed corpus — not data the
app can query per-claim. This slice turns it into a real, queryable **Claims Register**: a structured table,
a management API ("living bible" — propose / approve / edit / retire, lossless), and a seed that parses the
existing corpus into rows. **Data + CRUD + seed ONLY.** No detection, no UI (see OUT OF SCOPE).

## Existing patterns to mirror (build like these — Codex built on them in Phases 1–2)
- Model: `artemis/writing_rules/models.py` (`WritingProfile` ~46, `WritingRule` ~125, `WritingSource` ~204) —
  mirror the column/Base style. The register ties to a `WritingProfile` (FK `profile_id`).
- Repo: `artemis/writing_rules/repository.py` (list/get/create/update patterns).
- Routes: `artemis/routes/writing_rules.py` (`/api/writing-rules` prefix; `WritingRuleRead` schema style).
- Seed SOURCE: `artemis/writing_rules/seed_corpus.py` — the `"05_CLAIMS_REGISTER.md"` entry (~line 403) is
  the markdown to parse. Structure per claim: `## Claim NNN — <category>` then lines `Tier: <1-4>`,
  `Approved phrasing:` followed by a quoted string, `Packaging: <text|None>`, `Notes: <text>`.

## 1. Model + migration (additive, lossless — new revision off head 0071)
New model `Claim` (table `claims`):
- `id` PK; `profile_id` FK → writing_profiles (the register is per-profile).
- `claim_code` (text, e.g. "001"); `category` (text, e.g. "Identity / Category"); `tier` (int 1–4, nullable).
- `approved_phrasing` (text, NOT NULL — the canonical claim statement).
- `packaging` (text, nullable); `notes` (text, nullable); `source` (text, nullable — provenance/evidence).
- `status` (text, NOT NULL, default `'approved'`; allowed: `proposed` · `approved` · `retired`).
- `superseded_by` (FK → claims.id, nullable — lossless edits/retirement via supersession, never DELETE).
- `created_at` / `updated_at` (timestamptz, server_default now()).
Migration chains off **0071** (next number). downgrade drops the table. **No deletes anywhere.**

## 2. Repository
`list_claims(session, profile_id, status=None)`, `get_claim`, `create_claim` (default status `proposed` when
created via API — "AI proposes, human confirms"), `update_claim` (edit fields), `approve_claim`
(proposed→approved), `retire_claim` (status→retired, lossless; NOT a delete). Pure/deterministic.

## 3. API (NEW router file — `artemis/marketing/routes/claims.py`, prefix `/api/writing-studio/claims` — do
NOT add to writing_studio.py, to avoid colliding with terminal)
- `GET /api/writing-studio/claims?profileId=&status=` → list (default: all statuses; allow filtering).
- `GET /api/writing-studio/claims/{id}` → one.
- `POST /api/writing-studio/claims` → create (status defaults `proposed`).
- `PATCH /api/writing-studio/claims/{id}` → edit fields.
- `POST /api/writing-studio/claims/{id}/approve` → proposed→approved.
- `POST /api/writing-studio/claims/{id}/retire` → status→retired (lossless).
Register the router in the app the same way the other marketing routers are registered. Add a
`ClaimRead` Pydantic schema (+ create/update request schemas).

## 4. Seed
A seed function (callable, e.g. extend the existing seed import path or a dedicated
`import_claims_register(session, profile_id)`) that **parses the `05_CLAIMS_REGISTER` markdown** (from the
seed corpus / the stored `WritingSource`) into `Claim` rows with `status='approved'` (they're the approved
register), tied to the active profile. **Idempotent** (re-running doesn't duplicate — upsert on
`(profile_id, claim_code)`). Parse: `claim_code` + `category` from the `## Claim NNN — category` heading,
`tier`, `approved_phrasing` (the quoted string), `packaging`, `notes`.

## Acceptance (verify the EFFECT live — paste output)
- Migration up/down round-trips off 0071.
- Run the seed → `GET /api/writing-studio/claims?status=approved` returns the parsed claims (paste a couple,
  e.g. Claim 001 "Amira is the Learning Agent for Reading Growth.", with tier/category). Seed is idempotent
  (run twice → same count).
- `POST` a new claim → it lands as `proposed`; `POST …/approve` → `approved`; `PATCH` edits a field;
  `POST …/retire` → `retired` and it still EXISTS (lossless — GET by id still returns it).
- Unit/integration tests for the repo (status transitions, lossless retire) + the endpoints + the seed
  parser (a small fixture of the claim markdown → expected rows). `./scripts/check.sh` clean (note
  PRE-EXISTING failures separately — known ruff-format drift in unrelated files; list, don't fix).

## OUT OF SCOPE (do NOT build)
Claim DETECTION (matching draft text against the register — that's the composer's Stage-4 concern, separate);
the inline claim-flag UI; AI proposing claims from drafts; any composer/front-end work; touching the files
listed at the top.

## Constraints
Lossless (no deletes; edits/retire via status + `superseded_by`). Additive migration off head 0071 — **NOTE:
terminal may also add a migration in parallel; if so, Lead reconciles the chain at merge — just chain off
0071 and say so in your report.** New router file (no edits to writing_studio.py). Build on the writing_rules
model/repo/route patterns; don't fork. Org dep rule (no new deps expected). Isolated worktree + own test DB.
**Do NOT merge** — report branch + final SHA + worktree path + paste the seeded-claims output + the
propose→approve→retire lifecycle proof. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
Opus Lead reviews + verifies + merges.
