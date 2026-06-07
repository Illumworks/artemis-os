# Brief: Marketing Intelligence — Phase 1 trend substrate (Decisions 1 + 2)

**For:** the TERMINAL Opus Lead to orchestrate via parallel Sonnet workers.
**Back to:** app Opus Lead for live verification + merge to `main`. Local-only git.
Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

**THE SPEC LIVES IN:** `docs/marketing-intelligence-layer-design.md` → section **"Phase 1 —
concrete design (LOCKED with Jon 2026-06-04)"**. Read it in full + the "Locked principles" section
above it. This brief is the orchestration wrapper; the doc is the source of truth.

## What to build (summary; doc has detail)

Deterministic trend computation over EXISTING data (`signal_queue` + `districts`) — NO LLM produces
the numbers, NO new agents. Two decisions:
- **Decision 1** — enrich the Gate-1 / initiation surface (continuation of ENRICH1) with: momentum
  (signal counts time-series by theme × region + period delta), comparable-district count (90d),
  and past approve/reject of similar campaigns READ from the Phase-3 gate-decision/rejection memory
  observations.
- **Decision 2** — a ranked prioritization endpoint/view: districts/regions by signal velocity +
  urgency weighting + time-sensitivity (deadlines already in signals).

Persist computed trend snapshots as **memory observations** (the keystone is the home — accumulate,
don't throw away). Surfaces may also compute on-demand for freshness.

## Decomposition (suggested)

1. **Trends computation core** — deterministic aggregation module (counts/time-series/deltas/
   velocity/urgency-weighting by theme × region × time) + persist-as-observations + unit tests.
   FOUNDATION; build first.
2. **Decision-1 enrichment** — wire a trend block into the existing ENRICH1 Gate-1/initiation
   enrichment (reuse it, don't rebuild); pull past decisions from the Phase-3 memory observations.
3. **Decision-2 prioritization** — ranked endpoint + minimal view off the core.
(2 + 3 depend on 1 → build 1 first, then 2 + 3 in parallel.)

## Key files / substrate to reuse

- `artemis/marketing/models.py` (signal_queue: state, district, campaign_family, reason_codes,
  urgency_tier, evidence, dates), `districts`.
- ENRICH1 Gate-1 / initiation enrichment surface — `artemis/marketing/routes/initiation.py` +
  wherever `_build_district_context` / the initiation-context enrichment lives. **Find it; extend
  it; don't duplicate.**
- Memory read: `artemis/memory/retrieval.py search_observations` + the Phase-3 gate-decision /
  rejection observations (categories `signal_gate1_decision` / `pipeline_gate_decision`, scoped
  `agent:<slug>` / `workspace:marketing`). Write trend observations via the
  `artemis/builder/memory_carryover.py` pattern (multi-scope observation write).

## Guardrails

- DETERMINISTIC ONLY — trend numbers come from SQL/aggregation, never an LLM. (LLM narration is a
  later phase.) Every analytic must tie to Decision 1 or 2 — no metrics for their own sake.
- Each worker: own worktree, branch `worker/intel-p1-<piece>`, unit tests via
  `ARTEMIS_TEST_DB_URL=postgresql+asyncpg://artemis:artemis@localhost:5432/artemis_test uv run pytest`,
  ruff + ruff format + mypy clean. Workers do NOT run the live app, touch OKR, or add/upgrade deps.
- **Coordination:** a separate Codex fix is in flight on the deliverables pipeline
  (`briefs/fix-deliverable-candidate-misfire.md`) which may touch `initiation.py`. The app Opus lead
  will merge that FIRST; keep Decision-1 enrichment changes to the initiation *enrichment/context*
  code (disjoint from the deliverables-dispatch path) and flag any overlap.

## Handoff

Do NOT merge to main. Report each branch + diff + test results back to the app Opus lead, who
verifies LIVE (Gate-1 surface shows real trend context for a candidate; prioritization endpoint
returns a sensible ranked list) and merges. Log progress in `../claudeck-artemis/COORDINATION.md`.
