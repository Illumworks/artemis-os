# Platform Stewardship — Design Document

**Date:** 2026-05-30
**Author:** Lead (drafted with Jon during MC1+MW1 wait time, based on Clint Crigger's suggestion)
**Status:** DESIGN ONLY — not for implementation until prerequisites land. Captured for durability so when the time comes, the architecture is pre-thought.

---

## What this is

A scheduled platform-stewardship layer that runs weekly, inspects the platform's health across multiple dimensions, identifies areas for improvement, and surfaces findings to the operator. Findings flow into the existing Builder → Proposals Inbox → engine.commit() loop the platform already has — no parallel infrastructure required.

**Idea origin:** Clint Crigger (CEO, Illumadvisors) suggested the cron-scheduled health-check pattern to Jon. This document captures the design as it fits Artemis OS architecturally.

**Why now (for design, not build):** the substrate this layer consumes (memory keystone P4, anti-hallucination stack, Builder grounding tools) just landed this week. The right moment to think clearly about how stewardship fits is while the architecture is fresh, before the substrate hardens around an unconsidered shape.

---

## The four health dimensions

Stewardship operates across four orthogonal dimensions. Each has its own data sources, baseline definitions, and remediation paths:

### 1. Functions (correctness)

**What it measures:** are agents doing what their definitions say? Are tool calls succeeding? Are outputs structured correctly?

**Data sources:**
- `agent_run_trajectory_summaries.what_stalled` / `what_was_missing` — diagnostic narratives
- `tool_invocations.success=false` — failed tool calls
- `definition_proposals.status='rejected'` — operator-rejected changes (signals where the Builder was confused or wrong)
- Pydantic validation rejection counts (H2/H3/H4 surfaces — captured in logs today, would need to surface as metric)

**Remediation path:** Builder proposals via existing flow. The H1-H4 grounding work means proposed fixes won't introduce new hallucinations.

### 2. Speed (latency)

**What it measures:** how long does each piece of work take? Are agents getting slower over time? Is operator-perceived responsiveness degrading?

**Data sources:**
- `agent_runs.duration_ms` (already populated)
- `tool_invocations.duration_ms` (need to verify it's captured; if not, add)
- `ClaudeCodeTimeoutError` frequency (CC15 raised the timeout to 900s — track if anyone still hits it)
- Builder turn duration (FA + Builder both run through the adapter — we have the data)

**Remediation path:** mostly engineering (e.g., CC15 was Lead-fixed, not Builder-proposed). Some via Builder when the agent is doing unnecessary loops or oversized tool fetches.

### 3. Reliability (stability)

**What it measures:** failure rates, error patterns, recovery loops, orphan rates, dispatch failures.

**Data sources:**
- `agent_runs.status` distribution over time
- CC7's GC-orphan-recovery firings (track frequency — if it's firing often, dispatch isn't as durable as we thought)
- CC8 run-lock conflict counts (if many concurrent triggers, scheduler design has a flaw)
- App restart / process crash count (need to surface — likely via log aggregation)
- DB connection pool exhaustion incidents

**Remediation path:** engineering work + architectural decisions. Builder rarely helps here.

### 4. Drift

**What it measures:** are things that USED to work starting to break? Are agents producing different outputs for the same inputs over time?

**Data sources:**
- Trajectory summary deltas over time (compare "what worked" mentions in same-agent runs across weeks)
- `memory_conflicts` table (P1 substrate) — will be surfaced in MW3
- Signal qualification rate shifts (e.g., scout X qualified 60 signals/week, now 12)
- Reason-code distribution shifts per scout
- Tool argument shape drift (the LLM is starting to call tools with different parameters)

**Remediation path:** Builder proposals + manual operator triage. Drift is the hardest to detect and the most insidious — it's how subtle hallucination bugs leak in months later.

### 5. Resource utilization (cost / efficiency)

**What it measures:** token costs, subprocess churn, DB query patterns, claude-code cost data.

**Data sources:**
- claude-code adapter cost output (it does return cost_usd; CC-cost-dashboard banked from earlier)
- subprocess spawn rates per minute
- DB connection pool stats
- Memory observation storage growth

**Remediation path:** architectural decisions (responsiveness Phase 2 was about this — persistent subprocess instead of spawn-per-turn). Some via Builder if an agent is unnecessarily verbose.

---

## The hybrid local+cloud architecture (the key design decision)

Stewardship decomposes cleanly into three tiers. Each tier has different LLM requirements, different cost profiles, and different latency tolerances.

### Tier 1 — Pure compute (no LLM)

**Responsibilities:**
- Run metric queries against Postgres
- Compute statistical aggregations (means, p95, deltas vs baseline)
- Identify outliers via threshold rules ("scout X is 2 stddev below 7-day baseline")
- Detect drift via simple time-series comparisons
- Build the structured `HealthSnapshot` dataclass

**Cost:** zero LLM tokens. Fast (< 5 seconds for the full snapshot). Fully deterministic.

**LOC estimate:** ~400-600 (the bulk of stewardship is here).

### Tier 2 — Local LLM (prose generation)

**Responsibilities:**
- Generate human-readable summaries of each metric trend
- Categorize findings into themes ("performance issues" / "data quality" / "agent drift")
- Compose the Stewardship Report's prose sections
- Write headlines for individual findings

**Why local LLM is right for this tier:** prose summarization of structured data is exactly the workload where 7-13B models match larger models. No complex reasoning required — the data is already structured by Tier 1.

**Hardware fit:**
- Mac mini today (M2/M4, 16-32GB): can host Llama 3 8B, Mistral 7B, Phi-3 Medium via Ollama or MLX. Quality at this tier: **good enough**.
- Mac Studio future (M2/M3 Ultra, 64-192GB): can host Llama 3.1 70B (quantized), Qwen 2.5 72B. Quality at this tier: **excellent — close to Claude Sonnet** for prose tasks.

**Cost:** zero marginal $ (local). Speed limited by hardware.

**LOC estimate:** ~200 (adapter for Ollama/MLX + prompt templates + integration with Tier 1 snapshot).

### Tier 3 — Claude (deep reasoning, operator-triggered)

**Responsibilities:**
- Reason about WHY a pattern occurred ("why is brief_composer failing 3x more this week")
- Generate specific remediation proposals (feed the Builder)
- Cross-reference complex multi-agent patterns
- Suggest architectural changes (e.g., "consider co-proposing a `signal_queue.list` skill")

**Why Claude (not local) for this tier:** complex reasoning + tool use + grounding-against-DB-schema is where 7-13B and 70B class local models still trail Claude Sonnet significantly. The H1-H4 + CC20 grounding work was designed for Claude's quality bar.

**When this tier fires:** **only on operator action.** The weekly report (Tier 2) surfaces findings. Operator clicks "Investigate this" on a finding → Tier 3 fires → produces a Builder-grounded proposal.

**Cost:** subscription path (no per-token). Fires only on operator intent.

**LOC estimate:** ~150 (wraps the existing Builder flow + adds the "Investigate" UI action).

### The graceful degradation property

This architecture degrades cleanly when components are unavailable:

| If unavailable | Stewardship still produces |
|---|---|
| Tier 3 (Claude) | Weekly report (Tier 1+2). No deep investigation available; operator drives Builder manually instead. |
| Tier 2 (local LLM) | Structured snapshot only (Tier 1). Operator reads JSON or stats dashboard. No prose. |
| Tier 1 | Nothing — but Tier 1 is pure Python; if it's down, the whole app is down. |

**The architecture is resilient by design.** This is important when personal-instance distribution starts: some employee instances may not have local LLM set up; the weekly report degrades to "structured stats only" instead of breaking.

---

## Output shape — start narrow

### Phase 1 (initial fire)

**One memory observation per weekly run**, scoped to `workspace:platform`, category=`stewardship_report`:

- Headline summary (Tier 2 prose)
- 5-8 findings, each with:
  - Dimension (functions / speed / reliability / drift / cost)
  - Severity (informational / notable / urgent)
  - Cited evidence (links to specific agent_runs, tool_invocations, memory observations)
  - Suggested action (free text — "consider reviewing scout X's recent failures")
  - Confidence score

**No auto-proposals.** Operator drives any actual changes through existing Builder flow.

**UI surface:**
- Dedicated "Stewardship" section in the Memory shell sub-nav (after Memory Wings UI lands)
- Floating Artemis notification: "Your weekly stewardship report is ready" (links to the report)
- Optional Slack DM (if Slack integration is wired for notifications)

### Phase 2 (after Phase 1 proves value)

After ~8 weeks of Phase 1 data + observed signal-to-noise ratio:

- **Auto-propose** for findings with confidence > 0.9 + multiple cited evidence sources
- **Multi-agent stewardship** — split the single agent into specialized agents (run-health, memory-health, integration-health, cost-health)
- **Cross-instance reports** — when personal-instance distribution lands, aggregate stewardship across the fleet

**Don't build Phase 2 first.** Resist. Phase 1's value depends entirely on signal-to-noise ratio; don't compound noise prematurely.

---

## Prerequisites (must land before stewardship fires)

Stewardship reads from + writes to nearly every layer. Prerequisites:

| Layer | Status today (2026-05-30) | Why needed |
|---|---|---|
| Memory keystone P4 (M1-M6) | ✅ Complete | Provides the substrate for stewardship to read agent history |
| Anti-hallucination stack (H1-H4) | ✅ Complete | Ensures stewardship's own LLM outputs don't pollute findings |
| CC20 grounding tools | ✅ Complete | Tier 3 reasoning grounds against real schema/enums |
| Builder + Proposals Inbox | ✅ Complete | Operator-driven proposal flow already exists; stewardship findings feed into it |
| MC1-MC5 (carryover writes) | 🔄 In flight (MC1) | Stewardship can see approval history, not just emission history |
| MW1-MW4 (memory wings UI) | 🔄 Pending | Stewardship reports need a UI surface |
| H5 — Daily Brief Pydantic | 🔄 Pending | The Tier 2 prose generator needs the same anti-hallucination protection |
| 4-8 weeks of accumulated data | 🔄 Need time | Stewardship comparing week-N to week-N-1 needs at least week-N-1 to exist |

**Earliest practical fire:** 6-8 weeks from now, after MC1-MC5 + MW1-MW4 land + 4 weeks of memory accumulation provides comparison baselines.

---

## Brief sequence (when ready to fire)

5 surgical briefs, ~1100 LOC total. Each independently verifiable.

### SH1 — HealthSnapshot builder (Tier 1, pure compute) (~400 LOC)

`artemis/stewardship/snapshot.py` — module that runs the metric queries + aggregations and produces a `HealthSnapshot` dataclass.

- Per-dimension metric queries
- Outlier detection (threshold + statistical)
- Drift comparisons (week-over-week, scope-over-scope)
- Serialization to JSON for both the LLM tier and the UI

Tests: golden snapshots against fixture data. Property tests (snapshot is deterministic, snapshot is well-formed JSON).

### SH2 — Stewardship agent definition + scheduler (Tier 2 + Tier 3) (~300 LOC)

Define the stewardship agent in `agents` table with system prompt, tools (read-only access to HealthSnapshot + memory), grounding hooks (CC20 tools). Wire to APScheduler for weekly cadence + on-demand trigger via API endpoint.

The agent itself uses Tier 3 (Claude) for the initial implementation. Tier 2 (local LLM) is introduced in SH5.

### SH3 — Stewardship UI in Memory shell (~200 LOC)

`public/js/features/memory-shell.js` — new sub-section "Stewardship Reports" listing recent reports + per-finding detail.

Per-finding actions: "Mark as addressed" / "Investigate" (fires Tier 3 deeper analysis) / "Dismiss as noise" (feedback signal for the agent).

### SH4 — Floating Artemis stewardship notification (~80 LOC)

`artemis/floating_artemis/chat.py` — when a new stewardship report lands, FA proactively notifies in next conversation: "Your weekly stewardship report is ready. Want me to walk through the findings?"

### SH5 — Local LLM adapter (Tier 2 swap-in) (~150 LOC)

Only fire SH5 after Mac Studio arrives (or after local LLM quality validation on Mac mini).

`artemis/providers/ollama/` or `artemis/providers/mlx/` — new adapter. Routing rule: stewardship's Tier 2 prose generation uses local; Tier 3 stays on Claude.

This is also the brief that benefits the personal-instance distribution end goal — every employee instance can run Tier 2 locally.

---

## Architectural concerns + how the design addresses them

### 1. Improvement theater

**Risk:** stewardship produces polished findings that don't drive real change. Operator stops reading.

**Mitigation:**
- Track "approved-finding → measurable-metric-improvement" rate over first 8 weeks
- If <50% drive a measurable change, tighten the agent's criteria (raise confidence threshold, narrow focus)
- "Dismiss as noise" feedback in SH3 trains future reports to be quieter

### 2. Stewardship agent itself hallucinating

**Risk:** the LLM-driven layer is subject to the same hallucination class everything else was.

**Mitigation:**
- SH1 (Tier 1) is pure compute — no LLM, can't hallucinate
- SH2 (Tier 3 Claude) uses CC20 grounding tools (read_tool_signatures, read_db_schema, read_skill_catalog) — same protection the Builder has
- Tier 2 prose generation operates on the structured HealthSnapshot from SH1; can't invent facts because they're not free-form reasoning, just templated prose
- Pydantic schema on the report output (same H1-H4 pattern)

### 3. Race against active work

**Risk:** stewardship flags an issue Lead/operator is already fixing.

**Mitigation:**
- Stewardship agent checks `definition_proposals` for in-flight work first
- Skip findings that overlap with pending proposals
- Or weight findings DOWN if there's active work in the same scope

### 4. Cost (subscription stays flat but tokens still accrue)

**Risk:** weekly Tier 3 LLM run that reads N=200 agent_runs + memory observations is non-trivial token volume.

**Mitigation:**
- Tier 1 pre-aggregates — Tier 3 only sees summary metrics, not raw history
- Tier 2 (local LLM) absorbs the prose-generation token cost entirely when local-LLM hardware arrives
- Most weekly runs should be a few thousand tokens of Tier 3 input + 1-2k tokens output

### 5. Personal-instance distribution scaling

**Risk:** when 20 employees each run their own Artemis, central stewardship coordination explodes.

**Mitigation:**
- Each instance runs its own stewardship independently
- Tier 2 local LLM handles each instance's report generation
- Tier 3 fires only on per-operator-investigation requests
- Reports are scoped `workspace:platform` per instance — no central aggregation in v1
- Optional v2: a "fleet stewardship" agent that reads anonymized per-instance reports for cross-instance patterns

---

## Hardware sequencing (the Mac Studio question)

**Don't buy Mac Studio just for stewardship.**

Mac Studio is justified by many workloads:
- Faster Xcode compile times
- Video editing / audio processing
- Larger LLM hosting for any local-LLM use case (not just stewardship)
- More RAM for keeping multiple projects warm

If you buy Mac Studio for those reasons, the local LLM tier for stewardship is a **bonus payoff** — no incremental hardware cost.

**On current Mac mini:**
- Build Tier 1 today (works on any hardware)
- Run Tier 3 (Claude subscription) today
- Tier 2 deferred — but you can prototype with Llama 3 8B on Mac mini to see if quality is good enough for prose. Likely it is.

**The architectural commitment:** design Tier 2 as a clean adapter pattern from day one (SH2), even if it initially uses Claude. SH5 then swaps in local LLM later without architectural changes — just a provider config flip.

---

## Open design questions (for when ready to fire)

1. **Cadence:** weekly default, on-demand trigger. Should we also have monthly cadence for slow-moving drift metrics?
2. **Per-finding confidence threshold:** what cutoff makes Phase 2's auto-propose safe? My lean: 0.9 + at least 3 cited evidence sources.
3. **Stewardship agent's own grounding:** which CC20 tools does it need access to? Probably all of them — read_tool_signatures, read_db_schema, read_skill_catalog, search_memory.
4. **"Dismiss as noise" feedback loop:** does the agent learn from dismissals? Stores them in memory? Need to design.
5. **Cross-instance fleet stewardship:** how does the personal-instance distribution scale this layer? Probably v2 work; design considered but not committed.
6. **What does "successful stewardship week" look like as a metric?** Need a meta-metric. Lean: "weekly report had at least one finding that drove an operator-approved Builder proposal within 14 days."

---

## What to tell Clint

The idea fits Artemis architecturally because the platform already has every substrate it needs — memory, grounding, Builder proposals, approval surfaces. Without those substrates (which we spent this session building), a cron health-check would be net-negative (noise + pollution). With them, it becomes a real stewardship layer.

The hybrid local+cloud architecture is the right next-layer thinking. Local LLM absorbs the prose generation; Claude handles deep reasoning. Each personal-instance deployment scales without burning subscription tokens.

The Mac Studio question is real but shouldn't be a blocker — Mac mini can host the local tier with 7-13B models today; Mac Studio later future-proofs the harder reasoning. Either way, the architecture is the same.

**Design first, build later — exactly the right call.** When the prerequisites land (~6-8 weeks), the brief sequence is ready. Until then, the substrate it consumes keeps maturing.

---

## Status

**LOCKED 2026-05-30:** design captured for durability. Implementation deferred until prerequisites (MC1-MC5 + MW1-MW4 + 4 weeks of accumulated data) land.

When time to fire: SH1 → SH2 → SH3 → SH4, then SH5 (local LLM) when hardware/quality validation supports it.
