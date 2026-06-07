# Codex brief — Writing Studio build, Phase 1: Tag Registry (data + API + seed)

**Agent:** Codex. **Branch:** `worker/ws-tag-registry` off `main`. **Own git worktree, cwd inside it. Own
test DB** (`artemis_test_tagreg`: `createdb artemis_test_tagreg; psql -d artemis_test_tagreg -c "CREATE
EXTENSION IF NOT EXISTS vector;"; ARTEMIS_DB_URL=postgresql+asyncpg://artemis:artemis@localhost/
artemis_test_tagreg uv run alembic upgrade head; export ARTEMIS_TEST_DB_URL=...artemis_test_tagreg`).
**Do NOT merge — report.** Read `docs/AGENT-WORKING-PRINCIPLES.md` (root-cause, verify the effect).

## Scope (Phase 1 ONLY — the backbone the rest of the Writing Studio build attaches to)
Build the **data-driven tag registry**: tables + CRUD API + seed the locked vocabulary. Nothing else.
Full design context: `briefs/writing-studio-tagging-and-rules-engine.md`. **Out of scope (later phases, do
NOT build):** the tagging UI, the rules engine, AI auto-tagging, composer integration. Just the registry.

## 1. Migration (additive, lossless — new Alembic revision off current head)
Two tables:
- `tag_dimensions(id PK, key text unique, label text, active bool default true, sort_order int, created_at)`
- `tag_values(id PK, dimension_key text → tag_dimensions.key, value text, label text, parent_value text
  null (self-ref for subtypes), active bool default true, sort_order int, metadata jsonb default '{}',
  created_at)`; unique (dimension_key, value, coalesce(parent_value,'')).
Lossless: deactivation = `active=false`, never hard-delete. down_revision drops both tables.

## 2. Seed (idempotent — a seed script + invoked in the migration or a `scripts/seed_tag_registry.py`)
Exact locked vocabulary (Jon-approved):
- **dimension `asset_type`:** outreach email · email sequence · social post · blog · long form · product
  paper · landing page · webpage · impact story
  - subtypes (parent_value = the asset_type value):
    - email sequence → welcome/onboarding · nurture · re-engagement/win-back · event · demo or meeting
      follow-up · renewal/expansion · back-to-school/seasonal
    - long form → Decision Guide · Funding Guide · Field Guide · Product Explainer/Overview
- **dimension `audience`:** superintendent · district leader · curriculum director · principal · board
  member · special-ed director · teacher · parent  (parent's metadata: `{"applicable_platforms":["social"]}`)
- **dimension `platform`:** email · social · web/landing · print
- **dimension `intent`:** awareness · consideration · decision · expansion · credibility/proof
- **dimension `format`:** one-page · two-page · short · long  (note in label/metadata that format is
  flexible/extensible — these are starters, not an exhaustive/closed set)
(topic + geography are inherited from the campaign — NOT seeded here.)

## 3. API (FastAPI router, prefix `/api/writing-studio/tags`, `Depends(require_token)`; register in main.py)
- `GET ""` → all dimensions with their active values (nested), for the UI + agent.
- `POST "/dimensions"` `{key,label}` → create a dimension.
- `POST "/values"` `{dimension_key, value, label, parent_value?, metadata?}` → create a value.
- `PATCH "/values/{id}"` `{label?, active?, sort_order?, metadata?}` → edit / deactivate (active=false).
- `PATCH "/dimensions/{key}"` `{label?, active?}` → edit / deactivate.
Repository functions in a new `artemis/writing_rules/tag_registry_repository.py` (or extend the existing
`writing_rules/repository.py` — match the existing module style; don't fork patterns).

## Acceptance (verify the EFFECT — run it, don't just assert tests pass)
- `alembic upgrade head` then `downgrade` round-trips cleanly; re-running the seed is idempotent (no dupes).
- `GET /api/writing-studio/tags` returns all 5 dimensions with the seeded values + the email-sequence and
  long-form subtypes nested under their parents; `parent`/applicability metadata present.
- Add a value via POST → appears in GET; PATCH active=false → drops from the active list but the row still
  exists (lossless).
- Unit/integration tests for: migration, seed idempotency, each endpoint, the lossless deactivate, the
  unique constraint. `./scripts/check.sh` clean (ruff + mypy + tests); note any PRE-EXISTING failures
  separately (don't claim them as yours).

## Constraints
Lossless (deactivate, never delete; existing data unaffected). Org dep rule (no dependency <7 days old).
Additive migration only. Isolated worktree + own test DB (no contention). **Do NOT merge** — report the
branch + final commit SHA + worktree path + how each acceptance item was verified (paste the GET output).
Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.
