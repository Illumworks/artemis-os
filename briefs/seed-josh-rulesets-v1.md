# Brief — Seed faithful per-family rulesets from Josh's spec (v1)

**Type:** P0 seed (unblocks meaningful qualification scoring). **Model:** Codex or terminal Sonnet.
**Own worktree**, branch `worker/seed-josh-rulesets`, cwd INSIDE the worktree, branch off `main`.
**Own test DB** (develop + test there): `createdb artemis_test_seed; CREATE EXTENSION vector;
ARTEMIS_DB_URL=...seed uv run alembic upgrade head; export ARTEMIS_TEST_DB_URL=...seed`. Do NOT run
against the live DB — Lead applies it to artemis_os after merge.

## Goal

Write `scripts/seed_josh_rulesets.py` — idempotent, derives everything from `parse_spec()` (Josh's doc is
the source of truth; re-runnable when he updates it). Seeds per-campaign-family rulesets + territory config
so the qualifier produces meaningful, strategy-grounded scores instead of the empty `smoke-1` stub.

## What it must do

1. **Parse** `artemis.marketing.josh_spec.parse_spec()`. Use:
   - `spec.campaign_type_mappings` (§3): each has `campaign_type` + `reason_codes`. Canonicalize the family
     with `artemis.marketing.josh_spec.canonicalize_family(campaign_type)` → slug
     (obc / dyslexia / biliteracy / hit / general_growth).
   - `spec.reason_codes` (§2): each has `code` + `default_urgency` (a nuanced string).
   - `spec.territory_config.priority_states` (§1): the focus states (FL, IN, MD, MO, IL, TX).
2. **Build one ruleset per family.** `weighted_signals` = that family's reason codes (from §3 mapping),
   each entry `{"reason_code": CODE, "weight": W, "source": "josh_spec_v1"}`. Derive W faithfully from the
   code's `default_urgency` by PEAK tier mentioned in the string:
   - contains "hot" → **0.90**
   - else contains "standard" → **0.60**
   - else contains "enrichment" → **0.30**
   (This is a faithful numeric translation of Josh's tiers — keep it exactly this mapping so it's reviewable.)
   Set `version_tag="josh_spec_v1"`, `state="active"`, `hard_filters=[]`, `qualitative_rubrics=[]`.
   **Do NOT add a `state_not_excluded` hard filter** — it would hard-reject every non-priority-state signal;
   Josh's §4.1 hard skips (HMH/single-school/enrollment<5000) are NOT expressible in the current filter
   engine and are deferred to Phase 3 logic (note this in the script docstring). Territory focus is handled
   by the multiplier below, not a hard filter.
3. **Seed `territory_config`** (one row per family): `standard_states = priority_states`, `hot_states=[]`,
   default `unlisted_multiplier`. (Priority states get the standard multiplier; everyone else the 0.85
   unlisted penalty — a faithful soft focus on Josh's six states.)
4. **Idempotent upsert:** upsert ruleset by (family, version_tag) and territory_config by family — safe to
   re-run. **Deactivate any other active ruleset** (e.g. set `smoke-1` and stale `obc` drafts to
   `state='archived'`) so exactly one `josh_spec_v1` ruleset per family is active (no double-scoring).
5. **`--rescore-all` flag:** re-run `artemis.marketing.qualification.run_and_store_qualification` over ALL
   signals (not just pending) so existing signals get real fitScores under the new rulesets. Also keep a
   default mode that only seeds (no rescore). Self-bootstrap `sys.path` and use `settings.db_url` (NOT
   `database_url`) — both were bugs in the prior backfill script; don't repeat them.

## Verify (on your test DB)

- Seed a couple of `signal_reason_codes` + signals with known codes (e.g. `VENDOR_APPROVED_LIST`,
  `TX_HB1416_WAIVER`) in a priority state, run the script, then run qualification → the signal's
  `qualification_json` shows a non-zero `fitScore` ≈ the code's weight (≈0.9 for a hot code), and
  `signal_status='qualified'`. A signal with only an `enrichment` code (~0.3) does NOT pass min_fit (0.5).
- Re-running the script is a no-op (idempotent) — no duplicate rulesets, exactly one active per family.
- Print a summary table: family → #weighted_signals → version → state.

## Constraints
- Faithful to Josh: only his §3 family→code mapping + §2 tiers. No invented codes/families. Everything
  tagged `source="josh_spec_v1"`. Lossless — archive, don't delete, the old `smoke-1`/draft rulesets.
- No schema/migration (rulesets + territory_config columns already exist). Org dep rule: nothing <7 days
  old. ruff + mypy + a focused test for the script clean. Do NOT merge, do NOT touch artemis_os. Report
  branch + the summary table + how you verified the non-zero score. Trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
