# scout-adapter-regional-news — first real scout source

**Owner:** Sonnet Worker (isolated worktree via terminal-Lead)
**Branch:** `worker/scout-adapter-regional-news`
**LOC budget:** ~400 (full-diff insertions; hard stop at ~480 with headroom)
**Brief author:** Lead (Opus 4.7)
**Depends on:** M5b (scout_runner + SCOUT_SOURCE_ADAPTERS registry), M1 (reason codes), M5 (16 agent rows). All merged.
**Grounded in:** `artemis/marketing/scout_runner.py`, `artemis/marketing/scout_sources/regional_news.py` (stub), `artemis/scouts/regional_news/` (Node-style standalone implementation — reusable fetchers + classifier), `artemis/scouts/state_doe/sources.py` (RSS parser pattern), `artemis/pipelines/node_executors/agent_executor.py`.

## Why this brief exists

The Marketing Pipeline substrate is complete end-to-end: 22-node DAG, executor, live-view, approval cards, run history. But every pipeline run produces **zero real signals**, because:

1. `agent_executor.execute_agent_node()` routes `marketing.scout.*` agents through `builders/executor.run_agent()` — generic LLM chat. The scout's system prompt makes the LLM *talk like a scout*, but no real source data is fetched and no `signal_queue` rows are written.
2. `scout_runner.run_scout()` exists (M5b) and does the right thing — fetch via `SCOUT_SOURCE_ADAPTERS[slug].fetch()`, normalize via `scout_intake`, write `SignalQueue` rows — but **nothing calls it from the pipeline**. It's only reachable via the manual `POST /api/marketing/scouts/{agent_id}/run` endpoint.
3. Even if `scout_runner` were wired in, `SCOUT_SOURCE_ADAPTERS["regional_news"]` is `RegionalNewsAdapter(NullAdapter)` which returns `[]`. So the runner would loop zero times.

This brief closes both gaps for **one** scout (`regional_news`), making it the canonical reference implementation. The other 8 scouts stay stubs until separate adapter briefs land — but once this lands, every Marketing Pipeline run produces real signals, real Gate 1 cards, and a real brief.

## Scope

### Part A — Pipeline ↔ scout_runner bridge

**`artemis/pipelines/node_executors/agent_executor.py`** — detect scout agents and route them differently.

When `agent_id.startswith("marketing.scout.")`, instead of calling `run_agent()`:

```python
from artemis.marketing.scout_runner import ScoutMode, run_scout

scout_result = await run_scout(session, agent_id, mode=ScoutMode.scheduled)
```

Map `ScoutRunResult` → NodeState dict:
- `status`: `"succeeded"` if `scout_result.status` in `("complete", "skipped_locked")`, `"partial_complete"` if `partial_complete`, else `"failed"`.
- `cost_usd`: `scout_result.cost_usd`.
- `output_summary`: `f"Scout '{slug}' run {scout_result.run_id[:24]}: items={scout_result.items_processed} emitted={scout_result.signals_emitted} rejected={scout_result.signals_rejected}"`.
- Include `scout_run_id`, `signals_emitted` in the returned dict so downstream nodes (qualifier) can read it via `prior_<node_id>` injection.

Honor the existing per-node `cost_cap_usd` from `node.config` by passing it as `cost_cap_usd=` to `run_scout()`.

Honor `accumulated_cost_usd` cap as well — if `scout_result.cost_usd + accumulated_cost_usd > cost_cap`, return `partial_complete` like the existing branch.

**Do not change** how `run_scout()` works. Treat it as a sealed seam.

Non-scout agents continue through the existing `run_agent()` path unchanged. The branch is one `if` at the top of the function.

### Part B — Real `RegionalNewsAdapter.fetch()`

Replace the `NullAdapter` stub at `artemis/marketing/scout_sources/regional_news.py` with a working fetcher. Sources, in priority order:

1. **Google News RSS** (no API key). One query per priority state, filtered to literacy keywords. URL shape:
   `https://news.google.com/rss/search?q={url_encoded_query}&hl=en-US&gl=US&ceid=US:en`
   Query template: `"{state} schools" AND (literacy OR dyslexia OR "reading curriculum" OR superintendent OR RFP OR "board approved")`
   Pull ≤25 items per state to cap traffic.

2. **State DoE RSS** (no API key). Reuse `artemis/scouts/state_doe/sources.STATE_DOE_SOURCES` — for each priority state, hit `doe_rss` if set; skip gracefully on 404/parse error. The RSS parsing pattern in `state_doe/sources.py` (stdlib `xml.etree.ElementTree`, namespace-tolerant) is the reference; lift the helpers (don't rewrite). If a helper isn't import-safe from `artemis.marketing.scout_sources`, copy the minimal parsing function into a new `artemis/marketing/scout_sources/_rss.py` shared module (≤50 LOC).

3. **NewsAPI** (optional, only if `NEWS_API_KEY` env is set). Reuse `artemis/scouts/regional_news/client.fetch_news_articles` if possible — it already has the literacy-keyword filter and graceful empty-on-error contract.

**Priority states** come from `territory_config.hot_states + territory_config.standard_states`. The `fetch()` signature already accepts `territory_config: dict[str, Any] | None` — use it. When `territory_config` is `None`, fall back to a hard-coded `["FL", "TX", "IN", "MD", "MO", "MI", "IL"]` list (the union of states in `STATE_DOE_SOURCES`).

`last_run_at: datetime | None` is the second arg — use it to filter out items whose `published_at` predates the last successful run (dedupe across runs). When `None`, fetch everything.

### Part C — `RawItem` mapping

Each fetched item becomes one `RawItem`:

```python
RawItem(
    content=f"{title}\n\n{summary_or_description}",       # what the LLM sees
    source_url=link,
    source_title=title,
    source_published_at=iso_date_or_None,                  # YYYY-MM-DD
    metadata={
        "state": state_code,
        "source_kind": "google_news" | "state_doe_rss" | "newsapi",
        "source_name": rss_source_name_or_publisher,
    },
)
```

The scout's existing reason-code allowlist (`agent.reason_codes_emitted`, injected into the LLM system message by `scout_runner`) does the actual reason-code assignment. Don't pre-classify — that's the LLM's job per the M-series spec.

**One exception:** before returning a `RawItem`, run a coarse **relevance gate** to drop articles with zero literacy signal (keeps LLM cost down). Reuse `artemis/scouts/regional_news/client.LITERACY_KEYWORDS` and the `_contains_literacy_keyword` helper — lift them into the new adapter module, don't import from the standalone scout (which is separate machinery). This is a deterministic filter, not classification.

### Part D — Tests

`artemis/marketing/tests/test_scout_adapter_regional_news.py`:

1. **Stub adapter wiring test:** swap the registry's `RegionalNewsAdapter` for a fake that returns 3 hard-coded `RawItem`s. Run `run_scout()` with `adapter_override=fake`. Assert 3 rows land in `signal_queue` with `signal_status="pending_qualification"`. Reuses scout_runner; proves the bridge.

2. **Pipeline node test:** in `tests/pipelines/test_pipe4_executor.py` or a new `test_pipe4_scout_routing.py`, run a minimal 1-node pipeline whose only node is `agent_invocation` with `agent_id="marketing.scout.regional_news"`. Inject a fake adapter via dependency override or `SCOUT_SOURCE_ADAPTERS` monkeypatch. Assert node ends `succeeded` with non-zero `signals_emitted` in node state, and `signal_queue` has the rows. **This is the test that proves Part A.**

3. **Real RSS parse test** (no network — load fixture XML from `tests/fixtures/google_news_sample.xml` and `tests/fixtures/state_doe_fl_sample.xml`). Assert the parser extracts ≥1 item with title + link + published. Hand-craft fixture XML; do NOT make live HTTP calls in tests.

4. **Last-run dedupe test:** parser called twice with `last_run_at` after the fixture's pubDate → returns `[]`.

5. **Relevance gate test:** parser given an item with title "Local bake sale raises $200" → returns `[]` (no literacy keyword).

6. **Non-scout agent unaffected:** assert a non-scout `agent_invocation` node still routes through `run_agent()` (mock both `run_agent` and `run_scout`; only `run_agent` should be called).

Mock HTTP via httpx `MockTransport` — there's existing precedent in `artemis/scouts/tests/`.

## Acceptance criteria (the demo bar)

Hard requirement before merge: **on a clean DB**, click Run on the Marketing Pipeline, and:

1. ≥3 rows land in `signal_queue` with `source_type` ∈ `{news_article, state_doe}`, all with `signal_status="pending_qualification"`, all with non-null `reason_codes`.
2. The `scout_regional_news` node ends with `status="succeeded"` and `signals_emitted>=3` in node_state.
3. Downstream `qualifier_cross_reference` node fires (not skipped — it sees signals).
4. `content_brief_composer` produces a brief row referencing at least one of the emitted signals.
5. The pipeline suspends at `gate_brief_review` (Gate 1) with an approval card whose `payload.brief_preview` is non-empty and contains text from the emitted signals.

Worker reports each criterion explicitly with the run ID and signal IDs as evidence.

Note on flakes: Google News RSS is live network — the acceptance smoke is allowed to be a real network call. CI tests must use fixtures (criterion D-3). Run the smoke on the Worker's worktree against the local dev DB with `uv run alembic upgrade head` + `pkill -9 uvicorn && uv run uvicorn artemis.main:app --reload &` already established by terminal-Lead's invariants.

## Out of scope

- **The other 8 stub adapters** — separate briefs per scout. Don't even touch them. (Touching `_stub_base.py` is fine; it's shared infra.)
- **Scheduler wiring** — the existing `scout_scheduler` (M5b) is independent. Don't add cron triggers.
- **newsapi.org real call** — only the conditional `NEWS_API_KEY` enrichment; do not require the key.
- **BoardDocs / PDF parsing** — the standalone scout has it; regional_news adapter does not need it for v1. Banked for D3+.
- **Memory layer dedupe** (`memory_layer.upsert_last_seen`) — scout_runner already has a `TODO(memory_layer)` and degrades gracefully when the function doesn't exist. Do NOT invent the function. The `last_run_at` filter inside `fetch()` is sufficient dedupe for v1.
- **Reason-code classification logic** — the agent's LLM call + `reason_codes_emitted` allowlist handle this. Don't lift the deterministic `_classify` from the standalone scout's `mapping.py`.

## Invariants Worker must NOT regress

- `scout_runner.run_scout()` is a sealed seam — don't change its signature or behavior.
- `scout_intake.normalize_intake_payload` is the ONLY validation path.
- `dotenv override=False` invariant — both calls in `artemis/__init__.py`.
- conftest hard-fail on non-test DB (`f083ab4`).
- No `git push`.
- `pwd && git branch --show-current` before every state-changing Bash call (Worker is in an isolated worktree under `.claude/worktrees/agent-*/`).
- `git diff --stat` for LOC self-reporting — Workers have been overrunning briefs 1.4-3.4x; this brief has a hard cap at ~480 LOC. At ~400 LOC and one more file to add, stop and ping Lead via the report.
- `git diff --staged` reflex on any commit that includes a rename — bit us twice (`bc13611`, `720e2c8`).

## Files expected

- `artemis/marketing/scout_sources/regional_news.py` — rewrite from stub to working adapter. ~120 LOC.
- `artemis/marketing/scout_sources/_rss.py` — shared RSS parser + Google News URL builder + literacy keyword gate. ~80 LOC.
- `artemis/pipelines/node_executors/agent_executor.py` — add scout routing branch at top. ~30 LOC delta.
- `artemis/marketing/tests/test_scout_adapter_regional_news.py` — ~120 LOC.
- `tests/pipelines/test_pipe4_scout_routing.py` (or extend existing PIPE4 test) — ~60 LOC.
- `tests/fixtures/google_news_sample.xml` + `tests/fixtures/state_doe_fl_sample.xml` — hand-crafted RSS fixtures. ~40 LOC each.

Total expected: ~480 LOC. Brief allows up to ~480 with headroom; Worker should aim for ~400.

## How LLM cost stays bounded

`run_scout()` already enforces `DEFAULT_COST_CAP_USD = 1.00` per run. The pipeline node's `cost_cap_usd` overrides further if set. With the relevance gate dropping ~80% of news items before LLM call, and ≤25 items per state × ~7 priority states = ≤175 items pre-filter, the actual LLM-bound items will be ~35. At haiku rates (the seed agent's model) that's <$0.10/run. The cost cap is paranoia armor against a misconfigured query.

## Report Worker submits

1. `git diff --stat` output (full-diff insertions).
2. Output of `curl -s -X POST localhost:8000/api/pipelines/marketing.main/run | jq -r .id` — the run ID.
3. After the run completes, `curl -s localhost:8000/api/pipelines/marketing.main/runs?limit=1 | jq '.[0].nodeStates.scout_regional_news'` — node state with `signals_emitted` ≥3.
4. `psql artemis_os -c "SELECT id, source_type, headline, reason_codes FROM signal_queue WHERE provenance->>'run_id' = '<scout_run_id>' LIMIT 5;"` — paste the row dump.
5. The approval card snapshot at Gate 1 (`curl -s localhost:8000/api/approvals?gate_id=brief_review | jq '.[0]'`) showing `payload.brief_preview` populated.
6. Test pass count from `uv run pytest artemis/marketing/tests/test_scout_adapter_regional_news.py tests/pipelines/`.
7. `./scripts/check.sh` pass confirmation.
8. Branch + worktree path.
9. Anything that surprised you (RSS oddities, intake validation rejections, edge cases). The other 8 adapters will follow this pattern, so calibration matters.

---

**Lead notes (not for Worker):**

- This is the bridge between "substrate works" and "demo produces real artifacts." Once it lands, every demo of Marketing Pipeline shows real news → real signal → real brief → real Gate 1 card. That's the headline.
- The Part A branch (scout routing) is structurally the bigger architectural commitment. It's small in LOC but says "scouts execute via a different path than other agents." We're locking that in. If the Worker raises a concern that we should unify the paths instead — pull them up to Lead before they refactor.
- After this lands, the next adapters are simpler clones. `legislative` (Open States RSS) and `federal_funding` (grants.gov RSS) are the natural follow-ups — also no-key RSS sources. `starbridge_researcher` needs the Starbridge API key path; that one's a beat behind.
- If the Worker comes back saying "the pipeline already routes via X" or "scout_runner is already wired" — investigate before merging. The current state (`a3309674` run dump) shows scouts running but emitting zero signals; the LLM-only path is real. Don't take the Worker's pre-existing-state claim at face value.
