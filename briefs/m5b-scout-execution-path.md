# M5b — Scout Execution Path

**Owner:** Sonnet Worker (isolated worktree)
**Branch:** `worker/m5b-scout-execution-path`
**LOC budget:** ~350 (full-diff insertions; cap at ~400 with headroom)
**Brief author:** Lead (Opus 4.7)
**Depends on:** M1 (reason code registry, intake FK), M5 (16 agent rows seeded). Soft-depends on M3 (status transitions).
**Grounded in:** `docs/marketing-ops-v1/PIPELINE.md`, all 9 scout markdown files, existing `artemis/marketing/scout_intake.py`, existing `artemis.meetings.scheduler` (APScheduler pattern reference).

## Why this brief exists

M5 seeded 16 agent rows. Scouts have prompts, models, cadences. But there's no runner: nothing calls `scout.run()` on a schedule, nothing fetches Territory Config and passes it to the LLM, nothing handles the LLM response and writes to `signal_queue`. M5b ships that one execution path. It serves all 9 scouts — same code, different config — because every scout has the same shape: fetch context, invoke LLM, normalize output via `scout_intake.py`, write to `signal_queue`.

M5b is where Artemis OS goes from "16 agent definitions" to "9 scouts actively producing signals into the inbox." Without it, the marketing slab is read-only.

## Scope

### In scope

1. **`artemis/marketing/scout_runner.py`** — single execution path. Function: `run_scout(agent_id: str, mode: ScoutMode) -> ScoutRunResult`. Steps:
   - Load agent row by `agent_id` (errors if not seeded — clear failure mode).
   - Fetch context: Territory Config + Memory Layer last-seen for `(district, reason_code)` pairs + agent's source-specific inputs (Starbridge API client, news feed handler, etc. — pluggable per scout).
   - Resolve source adapter via a small registry (`SCOUT_SOURCE_ADAPTERS: dict[str, ScoutSourceAdapter]`) — one adapter per scout slug, returns raw items.
   - For each raw item: invoke LLM with the agent's `system_prompt` + the item + Territory Config + dedupe context. LLM must return JSON conforming to the Signal schema.
   - Pass LLM output through `scout_intake.normalize()` (existing). On validation failure: log to `unresolved_signals`, do NOT crash.
   - Write valid signals to `signal_queue` with `status='pending_qualification'`.
   - Update `agent_runs` row with run metadata: started/ended, items processed, signals emitted, signals rejected, cost, errors.

2. **Source adapter registry** — `artemis/marketing/scout_sources/`:
   - `base.py` — `ScoutSourceAdapter` ABC: `fetch(territory_config, last_run_at) -> Iterable[RawItem]`.
   - One thin stub per scout slug (9 files). Each stub returns an empty list AND logs "not yet implemented" — so the runner works end-to-end immediately, signals flow becomes real as adapters are filled in incrementally.
   - The Starbridge adapter (1.1) gets a slightly thicker stub: it imports `STARBRIDGE_API_KEY` from env, raises `NotImplementedError` if unset, fetches a placeholder list otherwise. Lets us smoke the end-to-end path with mock data when wiring Starbridge.
   - Adapters are pluggable: when Codex or a future Worker fills in `regional_news_scout`'s actual RSS feed handler, only that one file changes.

3. **APScheduler integration** — `artemis/marketing/scout_scheduler.py`:
   - `start_scout_scheduler()` / `stop_scout_scheduler()` matching the meeting scheduler pattern.
   - Registers one job per scout, cadence from the agent's `metadata.cadence_seconds` or default (4h = 14400 seconds for scheduled scouts; webhook-only scouts skip schedule registration).
   - Wired into `artemis/main.py` lifespan.

4. **Manual run endpoint** — `POST /api/marketing/scouts/{agent_id}/run` — triggers `run_scout()` synchronously, returns the `ScoutRunResult` JSON. Useful for testing and for the UI's "Run now" button.

5. **`ScoutMode` enum** — `scheduled` / `manual` / `backfill`. Passed into `run_scout()`, written to `agent_runs.metadata.mode`, lets reporting separate cadence-driven runs from human-triggered ones.

6. **Tests**:
   - Source adapter stub returns empty list → runner completes cleanly, zero signals emitted, agent_run row written with `signals_emitted=0`.
   - Mock adapter returns 3 valid items → 3 signals in `signal_queue`. Mock LLM responses.
   - Mock adapter returns 1 item that LLM produces invalid output for → 0 signals in `signal_queue`, 1 row in `unresolved_signals`, runner completes cleanly.
   - Scheduler registers 9 jobs (one per scout) on start; deregisters on stop.
   - Manual run endpoint returns 200 + result JSON; calling it on a non-existent agent_id returns 404.
   - Run mode written to `agent_runs.metadata.mode` correctly.

### Out of scope

- Real source adapter implementations beyond stubs. Each adapter is its own Worker brief later (M5c, M5d, …).
- Cost accounting beyond logging the LLM call cost on the agent_runs row.
- LLM call orchestration optimizations (batching, parallel item processing). Single-item for v1; optimize later if cost-per-signal exceeds budget.
- Webhook-driven scouts (Starbridge push, if it exists). Future brief.
- Backfill UI. The `backfill` mode is just an enum value — backfill orchestration is later.

## How LLM invocation works

Each scout has `system_prompt` (from the markdown's "Prompt scaffolding" block) and a `model` field. The runner uses the existing provider cascade (`resolve_adapter()` from O1's auth refactor at `e3ccf7e`) to call the LLM:

```python
adapter = resolve_adapter(agent.provider, agent.fallback_provider)
completion = adapter.complete(
    model=agent.model,
    system=agent.system_prompt,
    messages=[{"role": "user", "content": build_item_prompt(raw_item, territory_config, dedupe_ctx)}],
    response_format={"type": "json_object"},
)
signal_payload = json.loads(completion.text)
```

Then `scout_intake.normalize(signal_payload, scout_type=agent.agent_id.split(".")[-1])` enforces the anti-spoof + validation contract.

**Cost guard:** if `agent_runs.cost_usd` for the run exceeds a configurable per-run cap (default $1.00), stop processing further items, mark the run `partial_complete`, log a warning. Prevents a runaway LLM loop from burning the budget.

## Memory Layer integration

Each scout writes to `memory_layer` after emitting a signal: `(district_id, reason_code, embedding_hash, last_seen_at, signal_id)`. This is what the suppress_stale_signal rule (M4) reads.

For M5b: assume a minimal `memory_layer` repository function `memory_layer.upsert_last_seen(district_id, reason_code, embedding_hash, signal_id)`. If it doesn't exist yet, stub it with a TODO and document the gap in the report — do NOT block on it. The qualifier suppress rule degrades gracefully when the table is empty (no priors → no suppression).

## Invariants

1. **All 9 scouts go through `run_scout()`.** No scout-specific runner function. Differentiation lives in the source adapter + the agent row's prompt/model/cadence.
2. **Agent row is loaded fresh each run.** Don't cache. The Builder UI may have edited the prompt between runs.
3. **`scout_intake.normalize()` is the ONLY validation path.** Don't reimplement validation in the runner.
4. **Cost cap is checked per item, not per run total.** Prevents the case where one expensive item exhausts the cap and orphans the rest.
5. **Scheduler is idempotent.** `start_scout_scheduler()` called twice doesn't duplicate jobs. Match the meeting scheduler's existing pattern.
6. **The runner is reentrant.** Two concurrent runs of the same scout (manual + scheduled racing) don't double-emit. Use `SELECT FOR UPDATE` on the agent row OR a Postgres advisory lock keyed on `agent_id`.

## Files expected

- `artemis/marketing/scout_runner.py` — main runner. ~120 LOC.
- `artemis/marketing/scout_sources/__init__.py` — registry. ~20 LOC.
- `artemis/marketing/scout_sources/base.py` — `ScoutSourceAdapter` ABC + `RawItem` dataclass. ~30 LOC.
- `artemis/marketing/scout_sources/{starbridge,regional_news,linkedin,legislative,federal_funding,state_doe,procurement,board_minutes,leadership_transition}.py` — 9 stub adapters. ~10 LOC each × 9 = 90 LOC.
- `artemis/marketing/scout_scheduler.py` — APScheduler integration. ~50 LOC.
- `artemis/marketing/routes/scouts.py` — manual run endpoint (extend existing). ~20 LOC delta.
- `artemis/main.py` — scheduler lifespan hook. ~5 LOC delta.
- `artemis/marketing/tests/test_scout_runner.py` — tests. ~80 LOC.

Total budget: ~415 LOC. Brief allows up to ~450 with headroom; Worker should aim under 400 by keeping stub adapters truly minimal.

## Test plan

Six scenarios in the in-scope list above. Mocking:
- Source adapter: parametrize via dependency injection — pass a mock adapter into `run_scout()` for tests, not the real registry.
- LLM: monkeypatch `resolve_adapter().complete()` to return canned JSON.
- DB: real test DB (conftest hard-fail invariant in place).

Plus one integration smoke: spin up scheduler, advance time by one cadence tick (APScheduler supports this in test mode), assert one scout's `agent_runs` row was created.

## Invariants Worker must NOT regress

- conftest hard-fail on non-test DB (`f083ab4`).
- dotenv `override=False` (`7ad1598`).
- No `git push`.
- `pwd && git branch --show-current` before every state-changing Bash call.
- `git diff --stat` for LOC self-reporting.
- M5 seed must be runnable before tests — the test fixtures may need to run the seed in a setup step. Don't duplicate the seed logic in the test; import it.

## What "done" looks like

1. `run_scout(agent_id)` runs end-to-end against a stub adapter and writes 0 signals (because stub returns empty).
2. With a mock adapter returning 3 valid items, 3 rows land in `signal_queue` with `status='pending_qualification'`.
3. With a mock adapter returning 1 invalid item, 0 signals + 1 unresolved_signals row, runner exits cleanly.
4. Scheduler registers 9 jobs and deregisters on shutdown.
5. `POST /api/marketing/scouts/{agent_id}/run` returns 200 with the result; 404 for unknown agent.
6. Tests pass.
7. `./scripts/check.sh` does not regress.
8. Full-diff insertions ≤ 450. Over → stop and ping Lead.

## Report Worker submits

1. `git diff --stat` output.
2. The 9 scout source adapter slugs registered (paste).
3. The `run_scout()` signature + return type (paste).
4. Test pass count.
5. Branch + worktree path.
6. Any place where memory_layer.upsert_last_seen was needed but the function doesn't exist yet — flag for Lead, do not invent a schema. Stub with TODO comment.
7. Any place where M3's `transition()` would be the right call but M3 isn't merged yet — flag with TODO.

---

**Lead notes (not for Worker):**
- This is the brief that makes Artemis OS run live agents end-to-end. Cron tick → scout runs → signal emitted → qualifier rules (M4) → brief composer (existing) → inbox surface. Even with all 9 source adapters as stubs, the path works — feed it mock data via `POST /run` and watch a signal flow from raw item to inbox.
- The cost cap is paranoia armor against a misconfigured prompt that loops or returns runaway JSON. $1.00/run is generous; tune down once we have a feel for real costs.
- After M5b lands, the next obvious move is to fill in the Starbridge adapter for real (separate brief, M5c). Until then, mock data is fine for proving the loop.
