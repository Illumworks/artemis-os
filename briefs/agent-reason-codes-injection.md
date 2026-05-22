# Agent Reason Codes — DB Column + Runtime Injection

**Owner:** Codex (paste-ready, mechanical-leaning with one design call)
**Branch:** `codex/agent-reason-codes-injection`
**LOC budget:** ~150 (honest overrun OK to ~200)
**Depends on:** M5 seeded agents + M1 reason_code_registry merged.

## Why

3 of 9 marketing scouts have Josh's reason codes in their system_prompts; 6 don't. Not a bug per se (intake validates against registry regardless), but the LLM doesn't know its constrained vocabulary → may guess at codes → wasted invocations → failed signals.

Worse: future Josh updates to the registry require manually editing markdown prompts + re-seeding. Fragile.

**Fix:** add `agents.reason_codes_emitted` JSONB column. Runtime injects the current list into the system message at invocation time. Updates become SQL/UI edits, not prompt regeneration.

## Scope

1. **Alembic migration** — new column `agents.reason_codes_emitted` JSONB DEFAULT `'[]'`.
2. **M5 seeder update** (`artemis/marketing/seeds/marketing_agents.py`) — extract the "Reason codes emitted" section from each agent's markdown and populate `reason_codes_emitted`. Existing seed loader pattern; just add one more field.
3. **Agent Card UI** — show the emit list as multi-select dropdown sourced from `/api/signal-criteria/reason-codes`. Editable inline; saves via PATCH.
4. **Runtime injection scaffold** in `artemis/marketing/scout_runner.py` (and any future agent executor) — when building the LLM message, append:
   ```
   You may emit ONLY these reason codes: [POLICY_LIT_MANDATE, VENDOR_APPROVED_LIST, ...].
   Any other code will be rejected by intake validation.
   ```
   If the array is empty, fall back to "Any registered reason code is valid" (degrades to current behavior).
5. **Tests** — round-trip emit list via PATCH, runtime injection produces expected message, empty array degrades gracefully.

## Files

| File | LOC |
|---|---|
| `alembic/versions/<rev>_agents_reason_codes_emitted.py` | ~30 |
| `artemis/builders/models.py` (Agent model) | ~5 |
| `artemis/builders/schemas.py` (AgentRead/Update) | ~5 |
| `artemis/marketing/seeds/marketing_agents.py` | ~30 |
| `artemis/marketing/scout_runner.py` | ~15 |
| `public/js/features/operations-shell.js` (Agent Card emit-list editor) | ~50 |
| `public/css/features/operations.css` | ~15 |
| Tests | ~30 |

**Total: ~180 LOC.** Cap 220.

## Test plan

1. Migration up/down clean
2. M5 re-seed populates emit lists for 9 scouts; each gets the correct subset per markdown "Reason codes emitted" section
3. Agent Card shows multi-select with codes pre-selected
4. PATCH emit list → DB updates → reload reflects
5. Runtime: scout_runner builds message containing emit list text
6. Empty emit list → fallback message

## Invariants

- conftest hard-fail on non-test DB
- dotenv override=False
- node --check on modified JS
- git switch lead/j6a-granola-integration after commit
- Re-seed must preserve existing emit-list overrides (operator may have edited via UI; don't clobber)

## Report

git diff --stat, sample emit list for 1 scout, screenshot of Agent Card multi-select, test pass count, branch.
