# Codex brief — Writing Studio build, Phase 2: Tag-scoped rules engine

**Agent:** Codex. **Branch:** `worker/ws-rules-engine` off `main`. **Own git worktree, cwd inside. Own test
DB** (`artemis_test_rules`: createdb + `CREATE EXTENSION vector` + `ARTEMIS_DB_URL=...artemis_test_rules uv
run alembic upgrade head` + export `ARTEMIS_TEST_DB_URL`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` (root-cause; verify the effect live).

## Scope (Phase 2 ONLY — the rule-matching machinery on top of the Phase-1 tag registry)
Give writing rules a **tag scope** and a **resolver** that returns the rules matching a given set of tags.
This is the data + logic + API; it's testable standalone via a resolve endpoint. Design context:
`briefs/writing-studio-tagging-and-rules-engine.md` §6. **OUT of scope (later phases, do NOT build):** the
tagging UI, AI rule capture from conversation, and wiring the compose engine to call the resolver (that
needs drafts to carry tags, which is the composer phase). Just scope + resolver + API.

## Existing surface (build on it, don't fork)
- Model `WritingRule` — `artemis/writing_rules/models.py` (cols: profile_id, rule_type, title, body,
  status, …). **No scope field yet.**
- Repo — `artemis/writing_rules/repository.py`: `list_rules`, `get_rule`, `create_rule`, `update_rule`.
- Routes — `artemis/routes/writing_rules.py` (prefix `/api/writing-rules`): GET/POST `/rules`, GET/PATCH/
  DELETE `/rules/{id}`; schema `WritingRuleRead`.
- Phase-1 registry: `tag_dimensions` / `tag_values` (the allowed dimensions/values).

## 1. Migration (additive, lossless — new revision off head 0070)
Add `tag_scope jsonb NOT NULL default '{}'` to `writing_rules`. Shape: `{dimension_key: [allowed values]}`
(e.g. `{"audience": ["superintendent","board member"], "platform": ["email"]}`). **Existing rows stay
`{}` = global (always apply)** — do NOT auto-interpret existing rule text into scopes (that's a later manual
step; leaving them global preserves current behavior = lossless, no regression). downgrade drops the column.

## 2. Resolver (repository) — the core deliverable
`resolve_rules_for_tags(session, profile_id, tags: dict[str, str | list[str]]) -> list[WritingRule]`:
- Returns ACTIVE rules for the profile whose `tag_scope` matches `tags`.
- **Match semantics:** a rule matches iff, for EVERY dimension present in its `tag_scope`, the asset's
  `tags` has a value for that dimension that is IN the rule's allowed list (AND across dimensions, OR
  within a dimension). **Empty `tag_scope` ({}) = always matches** (global rule). A dimension in the rule's
  scope that the asset doesn't tag at all → no match for that rule.
- Pure + deterministic; this is what the composer/compose-engine will call later.

## 3. Schema + API
- Add `tag_scope` to `WritingRuleRead` + the create/update request schemas; thread through `create_rule` /
  `update_rule` so rules can be created/edited WITH a scope.
- New endpoint `POST /api/writing-rules/rules/resolve` body `{profileId, tags}` → the matching rules
  (the "which rules apply to this asset" call). This is how Phase 2 is verified end-to-end standalone.

## Acceptance (verify the EFFECT — run the resolve endpoint, don't just assert tests)
- Migration up/down round-trips; existing 3 seeded rules end up with `tag_scope = {}` and still resolve as
  global (lossless — they still apply to everything).
- Create a rule scoped `{"audience":["superintendent"],"platform":["email"]}` → `resolve` with
  `tags={"audience":"superintendent","platform":"email"}` RETURNS it; `tags={"audience":"teacher",...}`
  does NOT; `tags` missing `platform` does NOT.
- A global ({}) rule is returned for ANY tags.
- `POST /rules` with a tag_scope persists + round-trips via GET.
- Unit/integration tests for the resolver matrix (global, single-dim, multi-dim AND, OR-within-dim, missing
  dim) + the endpoints. `./scripts/check.sh` clean (note any PRE-EXISTING failures separately).

## Constraints
Lossless (additive column; existing rules stay global; no deletes). Build on the existing rule repo/routes,
don't fork. Org dep rule (nothing <7 days old). Additive migration only, chained off head 0070. Isolated
worktree + own test DB. **Do NOT merge** — report branch + final SHA + worktree path + paste the `resolve`
output proving the match matrix. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus
Lead reviews + verifies + merges.

## Note for the FOLLOW-ON (not this brief)
Once drafts carry tags (composer phase), wire `compose_engine.build_ruleset_grounding_block` to take
`resolve_rules_for_tags(draft.tags)` instead of all profile rules — so a draft is grounded only in the
rules that match its audience/type/platform. Keep that integration out of this slice.
