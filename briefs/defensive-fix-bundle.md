# Brief — Defensive fix bundle (Worker #1 of the planning roadmap)

**Type:** P0 latent-bug bundle, near-zero quality risk. **Model:** Codex or terminal Sonnet.
**Own worktree**, branch `worker/defensive-fix-bundle`, cwd INSIDE the worktree. Branch off `main`.
**Own test DB** (`artemis_test_defensive`: createdb + CREATE EXTENSION vector + `ARTEMIS_DB_URL=...defensive
uv run alembic upgrade head`; export `ARTEMIS_TEST_DB_URL`).

Source: `docs/provider-routing-cost-plan.md` Exec summary Quick Win #1 + Section 1 "Latent bugs". Six
silent failures that all stem from direct Anthropic SDK / empty-key call sites. Fix all six.

## The six fixes

1. **Repoint 2 broken agents** — Smoke Test Agent (#2) and WS Integration Agent (#1) have
   `provider='anthropic'` + NULL fallback → fail on first call (empty ANTHROPIC_API_KEY). Set
   `provider='claude-code'` and `fallback_provider` to a sane value. Do via an Alembic **data migration**
   (idempotent UPDATE by agent_id/name, guarded) — NOT a destructive change.
2. **Mock Post Gate (#172)** — `provider='claude-code'` but `fallback_provider=NULL`. Set a fallback in the
   same migration.
3. **`artemis/memory/graph_extractor.py:144`** — direct `anthropic.AsyncAnthropic()` with hardcoded
   `claude-haiku-4-5-...`. **This is why 238 observations are stuck `graph_status IS NULL` and the memory
   graph is empty** — every extraction silently failed (no key). Refactor to go through `resolve_adapter`
   (the provider abstraction → claude-code), mirroring the prior consolidator fix
   (`artemis/memory/consolidator.py` C3 pattern: `resolve_adapter("claude-code")` + run_turn, no raw SDK).
4. **`artemis/builders/workflow_executor.py:63`** — `AnthropicAdapter()` direct + hardcoded sonnet → route
   via `resolve_adapter`.
5. **`artemis/floating_artemis/tools/core.py:366`** (`spawn_subagent`) — `AnthropicAdapter()` direct →
   route via `resolve_adapter`.
6. **codex CLI on PATH** — `/Applications/Codex.app` is installed but `which codex` fails (Tier 2 gated on
   it). Add a symlink to a PATH dir (e.g. `~/.local/bin/codex` → the binary inside the .app). This is a
   local-machine step: do it AND document the exact command in your report so it's reproducible (it's not
   committed code). If you can't locate the binary inside the bundle, report the path you found and stop.

## NOT in this bundle (note, don't do)
- The graph-extraction **backfill** over the 238 NULL observations (re-runs LLM extraction — separate,
  costs throughput) → fast follow-on AFTER this merges + Lead live-verifies the extractor works.
- The 5 inline-cascade centralizations (Section 1 "Out-of-policy inline cascades") → separate cleanup.

## Verify (own test DB + targeted)
- Migration up/down round-trips; after up, the 2 agents are `claude-code` with non-NULL fallback and Mock
  Post Gate has a fallback. Re-running is idempotent.
- `graph_extractor`, `workflow_executor`, `spawn_subagent`: unit-test that they now obtain their adapter via
  `resolve_adapter` (no direct `AsyncAnthropic`/`AnthropicAdapter()` import-instantiation remains). Grep the
  three files to confirm the raw-SDK instantiation is gone.
- ruff + mypy clean; existing memory/builder/FA tests still pass.
- **Lead will live-verify** that graph extraction actually produces entities on a real observation after
  merge (worktree lacks claude-code auth). Report the exact command to run.

## Constraints
- Lossless: the migration only UPDATEs config columns; never deletes. No DELETE on observations/drawers/etc.
- Org dep rule: nothing <7 days old. Local-only git. Do NOT merge — report branch + SHA + worktree path +
  the codex symlink command + how Lead verifies the graph extractor. Trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
