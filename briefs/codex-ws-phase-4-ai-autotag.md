# Codex brief — Writing Studio build, Phase 4: AI tag SUGGESTION (propose-not-apply)

**Agent:** Codex. **Branch:** `worker/ws-ai-autotag` off `main`. **Own git worktree, cwd inside. Own test
DB** (`artemis_test_autotag`: createdb + `CREATE EXTENSION vector` + `ARTEMIS_DB_URL=...artemis_test_autotag
uv run alembic upgrade head` + export `ARTEMIS_TEST_DB_URL`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` (root-cause; verify the EFFECT — show the suggest call output).

## The point of this slice
Phase 3 lets a human tag a draft and grounds rules by those tags. Hand-tagging every draft is tedious — so
this slice adds **AI tag SUGGESTION**: given a draft's text + the tag registry, the model proposes the
best-fit `{dimension: value}` map. **It PROPOSES only — it does NOT persist.** The human confirms by calling
the existing Phase-3 `PUT …/tags`. This is the locked "AI proposes, human confirms" rule. No new dimension/
value is ever invented — the model must pick from the registry's allowed values.

## Existing surface (build on it, do NOT fork)
- Phase-3 tags (DONE): `GET/PUT /api/writing-studio/drafts/{id}/tags` in `artemis/marketing/routes/
  writing_studio.py`; `get_structured_tags_from_metadata` + `validate_structured_tags`
  (`artemis/writing_rules/repository.py` + `tag_registry_repository.py`). Tags live in
  `deliverable_metadata["structured_tags"]`.
- Registry (Phase 1): `list_tag_dimensions(session)` + `list_tag_values(session)` in
  `tag_registry_repository.py` — the allowed dimensions and their values.
- **LLM-call pattern to MIRROR** — already in this file, `compose_draft` (~lines 458–547 of
  `writing_studio.py`): `resolve_adapter(provider)` (from `artemis.providers.resolver`) → build
  `messages: list[Message]` with `make_user_msg(...)` → `await run_turn(adapter=..., messages=...,
  system=..., model=model_id, max_iterations=1)` (single-shot). Reuse this exact pattern; pull the model/
  provider off the active profile the same way (`default_model_provider` / `default_model_id`).
- Draft text: get the draft's current body the same way `compose_draft` builds its draft context (see
  `_latest_draft_content` in `compose_engine.py`); feed that text to the suggester.

## Deliverable — one new endpoint (WS drafts)
`POST /api/writing-studio/drafts/{draft_id}/tags/suggest` → returns proposed tags; **persists nothing**.
1. Load the deliverable (404 if missing). Get its body text. If there's no usable text, return
   `{"suggestions": {}}` (nothing to infer from — don't call the model).
2. Load the active registry: `{dimension_key: [allowed values]}` (active dimensions/values only).
3. Build a tight system+user prompt: "For each dimension below, choose the single best-fit value from its
   allowed list, or omit the dimension if the text doesn't clearly indicate one. Reply with JSON only:
   `{dimension_key: value}`. Use ONLY the listed values." Include the registry + the draft text.
4. `run_turn(..., max_iterations=1)`; parse the model's JSON out of the result text (be tolerant: strip code
   fences; if parse fails, treat as `{}`).
5. **Validate against the registry and DROP anything invalid** (unknown dimension or value the model
   hallucinated) — do NOT 400 on a bad model value; just omit it (log at debug). Reuse the registry
   allow-sets (same source `validate_structured_tags` uses). Return `{"suggestions": {dim: value, ...}}`.
6. **Do NOT write to `deliverable_metadata`.** Suggestion ≠ application.

## Acceptance (verify the EFFECT — paste the suggest output; mock the LLM in tests)
- With the adapter MOCKED to return `{"audience":"superintendent","platform":"email"}` for a draft whose
  text is about district leadership → `POST …/tags/suggest` returns exactly those (both in the registry).
  **And `GET …/tags` STILL returns `{}`** (proves propose-not-apply). Paste both.
- Mock returns a hallucinated value (`{"audience":"governor"}`, not in registry) → it's DROPPED; response is
  `{"suggestions": {}}` (or omits that dim), no 500.
- Mock returns junk / non-JSON / fenced JSON → handled gracefully, `{"suggestions": {}}`, no 500.
- Draft with no body text → returns `{"suggestions": {}}` WITHOUT calling the model (assert the adapter was
  not invoked).
- Confirm round-trip: take the suggestion, `PUT …/tags` with it (Phase-3 path), `GET` shows it persisted.
- Unit/integration tests with a mocked adapter (do NOT hit a real provider in tests). `./scripts/check.sh`
  clean (note PRE-EXISTING failures separately — known ruff-format drift in unrelated files; list, don't fix).

## Constraints
Propose-only — **never persists** (no metadata write in this slice). Lossless (no deletes, no migration —
no schema change needed). Build on Phase-1/3 helpers + the `compose_draft` adapter pattern; do not fork a new
LLM path. Tests MOCK the LLM (no live provider, no flakiness, no cost). Org dep rule (nothing <7 days old;
no new deps expected). Isolated worktree + own test DB. **Do NOT merge** — report branch + final SHA +
worktree path + paste the suggest output (valid case + hallucination-dropped case + the GET-still-empty
proof). Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies +
merges.

## Out of scope (do NOT build)
Auto-applying suggestions; a "suggest" button/UI (rides the composer phase); suggesting NEW registry values;
content-asset suggest endpoint (drafts only this slice); batch/bulk suggestion.
