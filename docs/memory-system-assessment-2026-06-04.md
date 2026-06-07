# Memory Keystone — Maturity Assessment & Roadmap Gut-Check (2026-06-04)

**Question asked:** Is the memory system finished — an "A/S-class" system that rivals top-performing
agent-memory systems (Mem0, MemGPT, Zep, LangGraph memory)?

**Verdict: NOT yet. It's a B-class *system* on an A-/B+ *substrate*.** The foundation is genuinely
top-tier; what's missing is the adaptive/learning + eval + semantic-integrity layer that makes
best-in-class systems feel alive. Closing it is a concrete 5-item punch-list — two of which are
~tens of lines of code.

## What's genuinely strong (A-/B+ substrate, BUILT + wired, no longer dormant)

- **Lossless + provenance:** append-only SHA-256 hash-chained raw_inputs; supersession-only
  retirement (never DELETE); evidence linking; full lineage. (`memory/raw_inputs.py`, `store.py`)
- **5-channel retrieval fusion:** FTS + semantic (pgvector/MiniLM-384, HNSW) + recency decay +
  composite score + graph 1-hop expansion, weighted-fused, ×confidence, ×log(evidence).
  (`memory/retrieval.py`, weights in `config/memory-retrieval.yaml`)
- **Consolidation:** LLM (Haiku) consolidation with heuristic pre-filter + incremental trigger
  (25-write threshold, 120s debounce). (`memory/consolidator.py`, `incremental_consolidator.py`)
- **Conflict framework + multi-scope (MW1) + working/durable wings + confidence model.**
- **Wired write + read paths** (no longer "built but dormant"): writes from trajectory (M1), FA
  turns (M3), signal/gate/definition/skill decisions + rejections (MC1–MC5, CC29), Phase-3 gate
  rejections, Phase-1 trend snapshots; reads by Agent-Builder (M2), Floating Artemis auto-inject
  (M4), pipeline rejection-context (C3), marketing-intel decision-history, the MCP server.

## The 5 gaps to A/S-class (ranked by impact)

1. **No feedback loop — `hit_count`/`accessed_at` never increment** (REAL but ~20 LOC, HIGHEST
   impact). Retrieval scoring weights `hit_count`, but no read path ever updates it → every
   observation is stuck at 0 forever. Memory never gets "stickier" from use. Top systems promote
   frequently-used memories. One UPDATE + async task in `search_observations()`.
2. **No retrieval eval/tuning harness** (REAL, high impact). Fusion weights are hand-tuned constants
   copied from the Node ref; no recall@k benchmark, no way to know if a tuning change helps or
   hurts. A/S-class systems have eval harnesses (often synthetic QA from stored observations).
3. **Conflict detection is string-heuristic only** (REAL, data-integrity at scale). Prefix/temporal/
   relation string matching; "budget is $50k" vs "fifty thousand allocated" won't be flagged.
   Needs cosine-threshold or an LLM adjudication pass. Stale/contradictory facts accumulate as
   volume grows.
4. **Maintenance/decay has no scheduler** (POLISH, ~10 LOC, real quality impact). `run_maintenance()`
   (category-aware score decay) is correct but nothing calls it → decay is inert → stale
   `discovery` observations never age out of the active pool. Needs a cron/startup periodic task.
5. **Writing Studio brand-voice memory is NOT connected to the keystone** (REAL, larger). The
   original P0 motivation. `compose_engine` uses separate `writing_studio_rules/examples` tables;
   zero `artemis.memory` imports. Connecting brand-voice recall to the keystone fulfills the
   original design + the most commercially visible use case.

**Quick wins (do first):** #1 + #4 ≈ 30 LOC combined, meaningful retrieval-quality jump.
**Real projects:** #2 (eval harness), #3 (semantic conflicts), #5 (WS↔keystone) — each its own focused effort.

## Roadmap gut-check — where the whole app stands (2026-06-04)

**DONE (desired functionality):**
- Marketing engine: signal → cluster → propose → initiate → draft → review/edit/approve (outbound flagged OFF).
- Slack Gate-2 approval cards (signal + content), approver DM + channel, approve-from-Slack, deep links (tunnel URL).
- Writing-Studio / self-training arc Phases 1–3: converse-with-AI compose, ruleset-grounded drafting,
  seed corpus, propose→approve learning loop, rejection→memory→agent-reads.
- Marketing Intelligence Phase 1 (Decisions 1 + 2): trend substrate + Gate-1 enrichment + "Where to focus" view (UI live).
- Pipeline robustness: cross-candidate deliverable misfire fixed; brief-less runs fail clean.

**BANKED / next (deliberately deferred):**
- Memory → A/S-class (the 5 gaps above).
- Intelligence Decision 3 (emerging-theme detection), alerts/light digest.
- Intelligence Phase 4 (campaign performance) — BLOCKED on outcome tracking (#106) + CRM connectors.
- Intelligence Phase 5 (reactive content) — now has a home (the self-training Writing Studio).
- Nuance/bug pass: clickable "Where to focus" rows (drill-down) + Jon's noticed items.

**Bottom line:** the app is essentially feature-complete for the desired scope. The single clearest
path from "great" to "best-in-class" is closing the memory gaps — and the foundation is strong
enough that it's a finite punch-list, not a rebuild.

---

## UPDATE 2026-06-04 — gaps #1 + #4 CLOSED (merged + verified live)

- **#1 retrieval feedback loop — DONE.** `search_observations` now records usage (hit_count+1,
  accessed_at=now) for the observations it returns, as a best-effort fire-and-forget async write
  (non-blocking on the hot path). Verified live on artemis_os: a real search bumped only the
  surfaced observations (186/143/142 → hit_count 1) and left an unsurfaced one (185) at 0; endpoint
  returned fast. Memory now gets "stickier" from use.
- **#4 decay scheduler — DONE.** `run_maintenance` wired to a daily APScheduler job
  (`memory_maintenance`, started at app lifespan) + `POST /api/memory/maintain` manual trigger.
  Verified live (decayed 188 observations). Decay is no longer inert.
- **REMAINING climb to A/S-class:** #2 retrieval eval/tuning harness, #3 semantic conflict
  detection, #5 Writing-Studio↔keystone connection. These are the bigger, deliberate efforts to
  plan as the next memory push.
