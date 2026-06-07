# Codex brief — Writing Studio build, Phase 3: turn the rules engine ON (resolver → compose grounding)

**Agent:** Codex. **Branch:** `worker/ws-rules-grounding` off `main`. **Own git worktree, cwd inside. Own
test DB** (`artemis_test_grounding`: createdb + `CREATE EXTENSION vector` + `ARTEMIS_DB_URL=...artemis_test_
grounding uv run alembic upgrade head` + export `ARTEMIS_TEST_DB_URL`). **Do NOT merge — report.** Read
`docs/AGENT-WORKING-PRINCIPLES.md` (root-cause; verify the EFFECT live, not just "tests pass").

## The point of this slice
Phase 1 built the tag registry; Phase 2 built `resolve_rules_for_tags` + `POST /api/writing-rules/rules/
resolve`. **Nothing calls the resolver yet** — every draft is still grounded in ALL of a profile's rules.
This slice makes a draft carry **structured tags** and makes the compose path ground it in **only the
matching rules** (resolver), falling back to all-rules when a draft is untagged (lossless — no behavior
change for today's untagged drafts).

## Existing surface (build on it, do NOT fork)
- Resolver (Phase 2, DONE): `artemis/writing_rules/repository.py` → `resolve_rules_for_tags(session,
  profile_id, tags: dict[str, str | list[str]]) -> list[WritingRule]`. Match semantics already correct
  (AND across dimensions, OR within, `{}`-scope rule = global/always).
- Grounding builder: `artemis/marketing/writing_studio/compose_engine.py` →
  `build_ruleset_grounding_block(profile, rules, examples)` — takes a `rules` LIST and formats it. **Do not
  change its signature**; change WHAT rules the callers pass in.
- **Two call sites** that currently pass ALL rules — these are the integration points:
  1. `artemis/pipelines/node_executors/agent_executor.py:~220` — `build_ruleset_grounding_block(profile,
     all_rules, all_examples)` (the campaign auto-draft path).
  2. `artemis/marketing/writing_studio/compose_engine.py:~346` (inside `build_writing_memory_prompt`) — the
     interactive compose path.
- Asset tags TODAY: campaign assets carry a FLAT `tags` list (campaign family + state) in the JSONB column
  `asset_metadata` (DB column `metadata`) — see `artemis/marketing/models.py:~399` and
  `artemis/marketing/writing_studio/invoke.py:~390`. That flat list is NOT the resolver shape. This slice
  adds the STRUCTURED shape alongside it; do not break the flat list.

## 1. Structured tags on a draft/asset (NO migration — reuse the JSONB column)
Store structured tags in the existing `asset_metadata` JSONB under a NEW key `structured_tags`, shape
`{dimension_key: value}` or `{dimension_key: [values]}` (e.g.
`{"audience": "superintendent", "platform": "email"}`). Additive + lossless (existing metadata + the flat
`tags` list untouched; absent key = untagged = `{}`). **No Alembic migration needed** — confirm this and say
so in your report. Validate keys/values against the Phase-1 tag registry (`tag_dimensions`/`tag_values`);
reject unknown dimension keys or values not in the registry with a 4xx.

## 2. Set/get a draft's structured tags (API)
Add endpoints on the existing writing-studio asset surface (find the current asset/draft routes; build on
them, don't fork a new router):
- `GET …/{asset_id}/tags` → the asset's `structured_tags` (`{}` if none).
- `PUT …/{asset_id}/tags` body `{tags: {dim: value|[values]}}` → validate against the registry, persist into
  `asset_metadata["structured_tags"]`, return the stored map. (This is "human sets the tags"; the
  AI-proposes-tags flow is a LATER slice — do NOT build it here.)

## 3. The integration (the actual payoff)
At BOTH call sites, before building grounding: if the asset/draft has non-empty `structured_tags`, fetch
rules via `resolve_rules_for_tags(session, profile_id, structured_tags)` and pass THAT list to
`build_ruleset_grounding_block`. If `structured_tags` is empty/absent, keep passing all rules (current
behavior — lossless). Examples handling is unchanged. Keep it a minimal, surgical swap of the rules source.

## Acceptance (verify the EFFECT — show it, don't just assert tests)
- Tag an asset `{"audience":"superintendent","platform":"email"}` via `PUT …/tags`; round-trips via `GET`.
- A profile with: one global rule (`tag_scope={}`), one scoped `{"audience":["superintendent"]}`, one scoped
  `{"audience":["teacher"]}`. After tagging the asset superintendent → the grounding block built for it
  contains the global + superintendent rules and **NOT** the teacher rule. Paste the grounding block (or the
  resolved rule titles) proving this.
- An UNTAGGED asset (`structured_tags` absent) → grounding contains ALL rules (unchanged). Prove the
  fallback.
- `PUT …/tags` with an unknown dimension/value → 4xx (registry validation).
- Unit/integration tests: tag validation (good + bad), set/get round-trip, and BOTH call sites'
  resolve-vs-fallback behavior. `./scripts/check.sh` clean (note any PRE-EXISTING failures separately — there
  is known pre-existing `ruff format` drift in ~10 unrelated files; list it, don't fix it here).

## Constraints
Lossless (additive JSONB key; flat `tags` list + existing metadata untouched; no deletes). NO migration if
the JSONB-key approach holds (confirm). Build on the existing resolver + grounding builder + asset routes —
do not fork or change `build_ruleset_grounding_block`'s signature. Org dep rule (nothing <7 days old; no new
deps expected). Isolated worktree + own test DB. **Do NOT merge** — report branch + final SHA + worktree
path + paste the grounding-block proof (scoped vs fallback). Trailer:
`Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Opus Lead reviews + verifies + merges.

## Out of scope (later phases — do NOT build)
AI auto-tagging (LLM proposes structured tags → human confirms) = Phase 4. The tagging UI = composer phase.
Re-tagging existing/historical assets. Mapping the flat `tags` list into structured tags automatically.
