# Cost Phase 1 — Foundation: `cost_events` table + instrumentation + pricing registry + backfill

**Paste-into:** terminal-Lead → Sonnet Worker (`Agent({isolation:"worktree"})`)
**Target branch:** `worker/cost-phase-1-foundation`
**Browser smoke owner:** Lead, post-merge — trigger each instrumented surface (agent run, FA chat turn, workflow run, memory consolidation, etc.) and verify a `cost_events` row lands per call.
**Report back to me by:** Jon pastes the relay.
**LOC cap:** ~450 + 1 alembic migration.
**Priority:** HIGH — foundation phase. Blocks all other cost phases. Everything else reads from this table.
**Parent plan:** `briefs/cost-page-design.md`
**Companion audit:** `audits/cost-page-audit.md`
**Depends on:** none.

---

## Why this exists

Per the audit, today's cost data is fragmented across three tables (`agent_runs`, `floating_artemis_messages`, `workflow_runs`) with different shapes. There's no unified call-level log. To answer "total spend across the app over time" requires UNION + GROUP BY on every page load, and feature attribution is impossible without a `feature_tag` column.

Per-model pricing is hard-coded in four separate locations (`artemis/builders/_cost.py`, `artemis/providers/openai/models.py`, `artemis/providers/gemini/models.py`, plus the Anthropic SDK pricing inferred from `_cost.py`). Rate changes require touching all four.

Phase 1 unifies both. After it lands:
- Every LLM call writes one row to `cost_events` at the call boundary with explicit provider, model, feature_tag, CLI-or-API flag, token counts, cache breakdown, and rate snapshot.
- All pricing lives in `artemis/costs/pricing.py`. The four existing locations import from there.
- A one-time backfill seeds the table from existing `agent_runs` + `floating_artemis_messages` + `workflow_runs` so the cost page has history from day one.

---

## Scope

### Part A — `cost_events` table + migration

New alembic migration. ONE new table:

```python
class CostEvent(Base):
    __tablename__ = "cost_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # When + identity
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)

    # Provider attribution
    provider: Mapped[str] = mapped_column(Text, nullable=False)        # 'anthropic' | 'openai' | 'gemini' | 'claude-code' | 'codex' | 'lm-studio'
    model: Mapped[str] = mapped_column(Text, nullable=False)           # canonical model id
    provider_path: Mapped[str] = mapped_column(Text, nullable=False)   # 'cli' | 'api'

    # Feature attribution
    feature_tag: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    # Allowed values: 'agent_run', 'floating_artemis', 'workflow', 'pipeline',
    # 'marketing_scout', 'marketing_brief', 'meeting_summary', 'memory_consolidation',
    # 'memory_graph_extraction', 'trajectory_summary', 'signal_qualifier',
    # 'background', 'unknown' (catch-all)

    source_kind: Mapped[str | None] = mapped_column(Text, nullable=True)   # 'agent_run' | 'fa_message' | 'workflow_run' | 'tool_invocation'
    source_id: Mapped[str | None] = mapped_column(Text, nullable=True)     # FK-ish reference; not enforced

    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    workflow_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)

    # Tokens
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cache_creation_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cache_read_input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")

    # Rate snapshot — frozen at call time so historical math doesn't drift
    input_rate_per_million: Mapped[float] = mapped_column(Float, nullable=False)
    output_rate_per_million: Mapped[float] = mapped_column(Float, nullable=False)
    cache_write_rate_per_million: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    cache_read_rate_per_million: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    # Cost (computed once, stored, frozen)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")

    # Optional context
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_error: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    error_kind: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("idx_cost_events_created_feature", "created_at", "feature_tag"),
        Index("idx_cost_events_provider_model", "provider", "model"),
    )
```

Indexes are time-series-friendly (range scans on `created_at` + feature filter) and breakdown-friendly (provider/model grouping).

### Part B — Pricing registry

NEW: `artemis/costs/__init__.py`, `artemis/costs/pricing.py`, `artemis/costs/events.py`.

**`pricing.py`** — single source of truth, immutable mapping per provider:

```python
PRICING = {
    "anthropic": {
        "claude-opus-4-7": {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_write": 3.75, "cache_read": 0.30},
        "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_write": 1.0, "cache_read": 0.08},
        # Prefix fallbacks for unknown sub-versions
    },
    "openai": {
        "gpt-5-mini": {"input": 0.25, "output": 2.0},
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        # ...
    },
    "gemini": {
        "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
        "gemini-1.5-pro": {"input": 1.25, "output": 5.0},
        # ...
    },
    "claude-code": {
        # CLI path: synthetic API rates apply (the CLI uses an Anthropic model under the hood;
        # rates fall through to the anthropic table for the actual model used)
    },
}

def get_rates(provider: str, model: str) -> dict[str, float]:
    """Return {input, output, cache_write, cache_read} rates per-million for (provider, model).

    Raises KeyError on unknown combo (callers must handle). Caches lookups.
    """
```

Rates are per-million-tokens (consistent units throughout the app — Gemini/OpenAI per-1k get converted to per-million when migrated in).

**`events.py`** — single helper to write a row:

```python
async def record_cost_event(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    provider_path: str,        # 'cli' | 'api'
    feature_tag: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    source_kind: str | None = None,
    source_id: str | None = None,
    agent_id: int | None = None,
    session_id: str | None = None,
    workflow_run_id: int | None = None,
    duration_ms: int | None = None,
    is_error: bool = False,
    error_kind: str | None = None,
) -> CostEvent:
    """Compute cost from rates, write the row, return it.

    Cost = (input * input_rate + output * output_rate + cache_creation * cache_write_rate + cache_read * cache_read_rate) / 1_000_000.
    """
```

### Part C — Instrumentation at call sites

Eight call sites need a `record_cost_event` call added after the LLM response lands. Each one is a small addition (~5-10 LOC).

**Agent runs:** `artemis/builders/executor.py:423` (where `cost_input_tokens`/`cost_output_tokens` are already set on `agent_runs`). Add a `record_cost_event` call alongside. `feature_tag='agent_run'`, `source_kind='agent_run'`, `source_id=str(agent_run.id)`, `agent_id=agent_run.agent_id`.

**Floating Artemis chat:** `artemis/floating_artemis/chat.py:1069` (where per-message tokens are written). `feature_tag='floating_artemis'`, `source_kind='fa_message'`, `session_id=session.id`.

**Workflows:** wherever `workflow_runs.total_cost_usd` is computed. `feature_tag='workflow'`, `source_kind='workflow_run'`, `workflow_run_id=run.id`.

**Memory consolidator:** `artemis/memory/consolidator.py:168` after the LLM response lands. `feature_tag='memory_consolidation'`.

**Memory graph extractor:** `artemis/memory/graph_extractor.py` — wherever the extraction call completes. `feature_tag='memory_graph_extraction'`.

**Trajectory summarizer:** `artemis/builder/trajectory_summarizer.py:180`. `feature_tag='trajectory_summary'`.

**Meeting summarizer:** `artemis/meetings/summarizer.py:290`. `feature_tag='meeting_summary'`.

**Marketing brief / scout / signal qualifier:** wherever each lands. `feature_tag='marketing_brief'`, `'marketing_scout'`, `'signal_qualifier'` respectively.

**Discovery step:** before writing the Worker brief, grep for ALL completion calls and confirm the list. If a call site doesn't fit cleanly into a feature_tag, use `'unknown'`.

**Pattern:** every adapter completion returns a usage object. Extract token counts → call `record_cost_event` → return as normal. Wrap in try/except so a recording failure doesn't break the parent call.

### Part D — Backfill script

NEW: `artemis/costs/backfill.py`.

```bash
uv run python -m artemis.costs.backfill --dry-run
uv run python -m artemis.costs.backfill
```

Sources:

1. **`agent_runs`** — one row per `agent_runs.id`. Derive `model` from `agents.model`. Provider = `anthropic`. Provider path = `'api'` for now (we don't have per-row CLI/API distinction historically; default to api as the more-expensive assumption). `feature_tag='agent_run'`. `created_at` = `agent_runs.started_at` or `completed_at`.
2. **`floating_artemis_messages`** — one row per assistant message with non-zero tokens. Derive `model` + `provider` from the linked `floating_artemis_sessions`. `feature_tag='floating_artemis'`. `created_at` = message timestamp.
3. **`workflow_runs`** — one row per workflow run with non-zero `total_cost_usd`. We don't have token detail historically; reverse-engineer from `total_cost_usd` using assumed rates and the workflow's preferred model. If too lossy, write a single row with token counts = 0 and `cost_usd = total_cost_usd` as a pre-aggregated event (and note this in the row's `error_kind='backfill_lossy'` if you want to flag it).

Idempotent: if a `cost_events` row already exists with the same `(source_kind, source_id)`, skip. Lets the backfill be re-run.

Dry-run mode reports per-source counts without writing.

### Part E — Tests

`artemis/costs/tests/test_pricing.py`:
1. `get_rates('anthropic', 'claude-opus-4-7')` returns expected rates.
2. Unknown model raises `KeyError` (caller handles).
3. Cache rate defaults to 0 for providers without prompt caching (e.g., gemini).

`artemis/costs/tests/test_events.py`:
4. `record_cost_event` computes cost correctly across 4 token streams.
5. Cost = 0 when all token counts are 0 (no division weirdness).
6. Recording failure doesn't raise to caller (try/except guard).

`artemis/costs/tests/test_backfill.py`:
7. Backfill produces N `cost_events` rows for N `agent_runs` rows.
8. Idempotent: re-run produces no duplicates.
9. Dry-run produces no writes.

`artemis/builders/tests/test_executor_cost_recording.py`:
10. A successful agent run produces both the `agent_runs` row AND a `cost_events` row with matching token counts.
11. An errored agent run still produces a `cost_events` row with `is_error=true`.

(Similar instrumentation tests for FA, memory consolidator, etc. — one per instrumented surface, ~6-8 small tests.)

---

## Files owned

- NEW: `alembic/versions/00XX_add_cost_events_table.py`
- NEW: `artemis/costs/__init__.py`
- NEW: `artemis/costs/pricing.py`
- NEW: `artemis/costs/events.py`
- NEW: `artemis/costs/models.py` (CostEvent ORM definition; can live in `events.py` if cleaner)
- NEW: `artemis/costs/backfill.py`
- EDIT: `artemis/builders/_cost.py` — migrate to import from `artemis.costs.pricing`
- EDIT: `artemis/providers/openai/models.py` — same
- EDIT: `artemis/providers/gemini/models.py` — same
- EDIT: `artemis/providers/claude_code/adapter.py` — pricing lookup via registry
- EDIT: `artemis/builders/executor.py` — record_cost_event call after Anthropic response
- EDIT: `artemis/floating_artemis/chat.py` — record_cost_event call after each turn
- EDIT: `artemis/memory/consolidator.py` — same
- EDIT: `artemis/memory/graph_extractor.py` — same
- EDIT: `artemis/builder/trajectory_summarizer.py` — same
- EDIT: `artemis/meetings/summarizer.py` — same
- EDIT: marketing / scout / signal qualifier modules — same (Worker grep + confirm exact files)
- NEW: `artemis/costs/tests/test_pricing.py`
- NEW: `artemis/costs/tests/test_events.py`
- NEW: `artemis/costs/tests/test_backfill.py`
- NEW: per-surface instrumentation tests as listed above

---

## Acceptance criteria

1. **Migration applies cleanly.** `uv run alembic upgrade head`. `\d cost_events` shows the table. **Paste.**
2. **All tests pass.** `ARTEMIS_TEST_DB_URL=… uv run pytest artemis/costs/tests/ artemis/builders/tests/test_executor_cost_recording.py -v`. **Paste.**
3. **Type + lint pass.** `./scripts/check.sh`. **Paste.**
4. **Backfill dry-run report.** `uv run python -m artemis.costs.backfill --dry-run` — paste the per-source row counts.
5. **Backfill real run.** `uv run python -m artemis.costs.backfill` — paste post-run total row count.
6. **Live smoke (Lead does post-merge):**
   - Trigger one agent run end-to-end. Verify a `cost_events` row lands with non-zero tokens + cost.
   - Send one FA chat message. Verify a row lands.
   - Trigger one memory consolidation (or wait for the next scheduled one). Verify a row lands.
   - Trigger one trajectory summary. Verify a row lands.
   - Run `SELECT feature_tag, COUNT(*), SUM(cost_usd) FROM cost_events WHERE created_at > now() - interval '1 hour' GROUP BY feature_tag;` — paste the result. Expect rows for at least 4 different feature_tags.
7. `git diff --stat` shows the 8 instrumented files + new module. **Paste.**

---

## Hard constraints

- **Lossless audit.** No DELETE on `cost_events`. Even errored calls write rows (with `is_error=true`).
- **Rate snapshot is frozen.** Once a row is written, `input_rate_per_million` etc. don't change even if `pricing.py` updates later. The page reads stored rates for historical math.
- **Recording failure never propagates.** Each `record_cost_event` call is wrapped in `try/except` and logs WARNING on failure. The LLM call's result is what the user sees.
- **Backfill is opt-in and idempotent.** Lead runs it manually; not part of `alembic upgrade head`. Idempotent on `(source_kind, source_id)` so re-runs are safe.
- **Provider path is explicit.** Every row says `provider_path = 'cli' | 'api'`. No null.
- **Feature tag uses the canonical set** in the table schema. Catch-all = `'unknown'` only when no fit; treat as a backlog signal to refine the taxonomy.
- **No fancy aggregations in this phase.** Phase 1 writes raw events. Phase 2 builds the rollup endpoint. Don't bundle.
- **Migration is single-table, additive.** Don't touch `agent_runs` / `floating_artemis_messages` / `workflow_runs` schemas. They stay as they are and `cost_events` becomes the source of truth going forward.
- **Local-only git.** Worker on `worker/cost-phase-1-foundation`; Lead merges after smoke.
