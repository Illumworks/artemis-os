# Opus Lead — review handoff for three planning workstreams

**Generated:** 2026-06-06 (terminal Opus 4.7, planning sessions with Jon)
**Audience:** Opus Lead (next claude-code session Jon spawns)
**Your job:** Review the artifacts below. Validate or push back on the decisions. Propose a Worker brief sequence with dependencies. Then spawn the first Worker. **No code in this review pass.**

---

## What I did across three planning sessions

Jon and I planned three coupled workstreams. All artifacts are read-only docs and Worker-ready briefs in `briefs/` and `docs/`. No code was written.

### Workstream 1 — Memory page redesign

The Memory page currently renders a flat list of "coded memories" with no provenance, no categories, no actions, no entity browser. Backend has all the data; the UI exposes ~10% of it. There's also ~1,500 lines of dormant code in `memory-shell.js` from a pre-M6 design that doesn't render.

**Deliverables to review:**
- `audits/memory-ux-audit.md` — findings + the dormant-code situation + appendix on empty graph layer
- `briefs/memory-ui-redesign.md` — master plan with locked decisions
- `briefs/memory-phase-2-provenance-lineage.md` — **ships first** (Jon's call)
- `briefs/memory-phase-1-rows-speak.md` — category badges, score, filters, search
- `briefs/memory-phase-3-curate-conflicts.md` — Pin / Confirm / Retire (with reason) / Supersede + Conflicts drawer (only phase with write paths)
- `briefs/memory-phase-4-scope-tree-recent.md` — left-rail scope tree + "Recently added" feed
- `briefs/memory-phase-6-voice-fa-handoff-health.md` — voice copy + FA handoff + Health tab + dormant-code cleanup
- `briefs/memory-phase-5-prereq-graph-extractor-audit.md` — **root cause now known** (see Workstream 3)

**Locked decisions (validate):**
1. Phase 2 (provenance) ships before Phase 1 — emotional first-impression wins
2. Phase 3 ships both Pin AND Confirm (different purposes)
3. Retire requires a free-text reason
4. Phase 5 deferred until graph layer populates (root cause now known — see W3)
5. Header label stays "Memory"; voice lives in the pulse line

### Workstream 2 — App-wide Cost page

A dedicated full-page Cost surface accessed from the profile menu (not the left rail). Backend has all the token data needed; the existing `cost-dashboard.js` calls an endpoint that was never built. Multi-provider is "wired infrastructure but operationally unused" — today essentially 100% of spend is Anthropic-routed.

**Deliverables to review:**
- `audits/cost-page-audit.md` — fragmented data, dormant frontend, multi-provider gap
- `briefs/cost-page-design.md` — master plan with 12 locked decisions
- `briefs/cost-phase-1-foundation.md` — `cost_events` table + instrument ~8 call sites + central `pricing.py` + one-time backfill
- `briefs/cost-phase-2-visibility-dashboard.md` — profile-menu "Cost" item + full-page route + Spend tab
- `briefs/cost-phase-3-routing-opportunities.md` — **updated this session** — now includes Apply buttons + availability filtering
- `briefs/cost-phase-4-cloud-infra-tab.md` — Fly.io reference projections
- `briefs/cost-phase-5-forecast.md` — trailing-7d monthly projection
- `briefs/cost-phase-6-alerts-budgets.md` — soft thresholds + in-app alerts
- `briefs/cost-prereq-multi-provider-activation.md` — **partially superseded by Workstream 3** (see below)

**Locked decisions (validate):**
1. New unified `cost_events` table + one-time backfill from existing tables
2. Synthetic API cost is the primary lens; actual CLI subscription cost shown as secondary
3. Hero answers "How much did I spend this month" with prior-month comparison
4. Spend broken down by source bucket (Agents / FA / Workflows / Pipelines / Marketing / Memory / Scout / Background)
5. Phasing: Phase 1 visibility, Phase 2 forecast, Phase 3 alerts+budgets
6. Honest dashboard + Routing opportunities panel (not just hidden truths)
7. Fly.io as the cloud-infra reference; Cloudflare can front static assets only
8. Full shell route accessed from profile menu (between Connectors and divider)

### Workstream 3 — Provider routing audit + self-service routing surface

Auditing the multi-provider strategy revealed that **infrastructure is wired but operationally aspirational**. Found 5 latent bugs, including the root cause of the memory session's empty graph layer.

**Deliverables to review:**
- `docs/provider-routing-cost-plan.md` — full call-site inventory (24 sites) + 3-tier philosophy + ranked quick wins + work philosophy + API-readiness numbers + revised LM-Studio-vs-Gemini per-task cascade
- `briefs/routing-control-surface.md` — **new this session** — dedicated Routing page (profile menu) + `feature_routing_overrides` table + `provider_health` module + resolver patch

**Key findings (validate, then we act):**
1. **`codex` CLI is installed at `/Applications/Codex.app` but not on PATH.** Tier 2 of the routing strategy is gated on a one-line symlink fix.
2. **LM Studio is live** on `:1234` with `qwen3-14b` + `qwen2.5-coder-14b`. Tier 3 local works today.
3. **Only `ANTHROPIC_API_KEY` and `OPENAI_API_KEY` are in `.env` — both empty.** `GEMINI_API_KEY` isn't even defined. So today only `claude-code` (subscription) + `lm-studio` (local) are reachable.
4. **Five latent bugs** that fail silently with empty API keys:
   - Smoke Test Agent #2 + WS Integration Agent #1 (`provider='anthropic'`, no fallback)
   - `artemis/memory/graph_extractor.py:144` — direct `AsyncAnthropic()` SDK call. **This is the root cause of the memory session's mystery: 238 observations stuck at `graph_status IS NULL` because every consolidation's graph extraction has silently failed.**
   - `artemis/builders/workflow_executor.py:63` — hardcoded `AnthropicAdapter()` + `claude-sonnet-4-6`
   - `artemis/floating_artemis/tools/core.py:366` (spawn_subagent) — same anti-pattern
   - Mock Post Gate agent #172 has NULL fallback_provider
5. **Five inline cascade duplications** — drift risk; should centralize through `resolve_adapter()` eventually
6. **Tier 3 is a cascade, not a single provider.** Per-task: LM-Studio-first for privacy/simple/high-volume; Gemini-Flash-first for JSON-strict/concurrent/long-context. Free-tier Gemini API is available to Jon.

**Top 5 ranked quick wins (in `docs/provider-routing-cost-plan.md` Section 0):**
1. **Defensive fix bundle** — repoint 2 broken agents, refactor 3 hardcoded `AnthropicAdapter()` sites, set Mock Post Gate fallback. **No quality risk.** Recommend as the FIRST Worker brief.
2. Codex CLI on PATH — one symlink, unblocks Tier 2
3. Memory consolidator → Gemini Flash → LM Studio cascade
4. Trajectory summarizer → LM Studio → Gemini cascade
5. District Classifier scout → LM Studio cascade

---

## Critical connections you'll otherwise miss

- **Memory Phase 5 prerequisite's "why no entities" mystery** = `graph_extractor.py:144` empty-key SDK bypass. The defensive fix bundle solves it. Memory Phase 5 unblocks once the graph extractor is routed properly AND a backfill runs over the 238 NULL-status observations.
- **`briefs/cost-prereq-multi-provider-activation.md`** (from the prior session) is mostly superseded. It proposed a code-level `feature_cascades.py` config. The DB-backed override mechanism in `briefs/routing-control-surface.md` replaces that approach entirely. After routing-control-surface ships, cost-prereq becomes a tiny "seed initial overrides via UI/API" task. **Recommend you rewrite or retire cost-prereq.**
- **`briefs/cost-phase-3-routing-opportunities.md` depends on `briefs/routing-control-surface.md`** — Apply buttons hit the foundation's endpoints. Order matters: routing-control-surface first.
- **Memory Phase 6 includes a ~1,500 LOC deletion** of dormant `memory-shell.js` code. Do it as a separate commit so it's revertable. Audit-trail discipline.

---

## My recommended Worker brief order

Two independent foundations can ship in parallel (different Workers, no shared files):

```
Phase A1 ─ DEFENSIVE FIX BUNDLE (NEW BRIEF YOU SHOULD WRITE)
            ├ 5 latent bug fixes (2 broken agents, 3 hardcoded adapters, NULL fallback)
            ├ codex CLI PATH symlink
            └ ~150 LOC + a SQL migration for the agent fixes
            (Quick win #1 from docs/provider-routing-cost-plan.md — most urgent)

  ┌──── parallel ────┐
  │                  │
  ▼                  ▼
Phase A2          Phase A3
Cost Phase 1      Routing Control Surface
Foundation        (briefs/routing-control-surface.md)
(cost_events)     (feature_routing_overrides + Routing page)

  │                  │
  └──── parallel ────┘

Phase B
Cost Phase 2 Visibility Dashboard (depends on A2)
Memory Phase 2 Provenance & Lineage (independent; can ship anytime)

Phase C
Cost Phase 3 Routing Opportunities (depends on A2 + A3 + B)
Memory Phase 1 Rows Speak (depends on Memory Phase 2)
Memory Phase 3 Curate + Conflicts (depends on Memory Phase 2)

Phase D
Memory Phase 4 Scope Tree (depends on Memory Phases 1, 2, 3)
Cost Phases 4-5 Cloud Infra + Forecast
Memory Phase 5 prereq (graph extractor) — partially solved by defensive bundle; needs the backfill script

Phase E
Memory Phase 6 (voice + FA handoff + cleanup) — last
Cost Phase 6 Alerts + Budgets — last
```

The defensive fix bundle is small but high-value: zero quality risk, fixes failures, unblocks Tier 2 routing, solves the memory graph mystery.

---

## Your decision asks (4 total)

From `docs/provider-routing-cost-plan.md` Section 10:

1. **Greenlight the defensive fix bundle as Worker brief #1?** Write it from `docs/provider-routing-cost-plan.md` Quick Win #1.
2. **Greenlight LM Studio + Gemini offloads (Quick Wins #3-5) as a bundle or one at a time?** Each needs a 3-way validation script (claude-code vs LM Studio vs Gemini) per the appendix in the plan doc.
3. **Rewrite `briefs/cost-prereq-multi-provider-activation.md`?** Per the supersession above. My recommendation: rewrite into a tiny "seed initial overrides" brief that runs after routing-control-surface lands.
4. **Greenlight the routing self-service surface pair?** (`routing-control-surface.md` + updated `cost-phase-3`.) Both Workers can run after foundations land.

---

## Process expectations

- **No code in this review.** Read everything. Push back where you disagree. Propose adjustments to me (the planner) or directly to Jon if they're cosmetic.
- **Spawn Worker briefs in dependency order.** Per the diagram above. The defensive fix bundle goes first because zero risk + unlocks downstream.
- **Browser-smoke every merge.** Per the hard constraints in each brief, you (Lead) browser-smoke each Worker's output before declaring done. Live-data smokes catch real bugs that tests miss (per the user's `feedback-live-smokes-catch-real-bugs` memory).
- **Lossless audit discipline.** Any new table you introduce follows the lossless rule per CLAUDE.md — no DELETE on observations, drawers, cost_events, routing overrides, change log rows.
- **Local-only git.** All branches and commits stay local. No remote push.
- **Match the per-agent test DB pattern.** Per `feedback-cross-agent-test-db-contention` — if you spawn parallel Workers, give each its own test DB to avoid TRUNCATE deadlocks.

---

## Quick acceptance check for you

Before spawning the first Worker, validate:

- [ ] You've read the three master plans (`memory-ui-redesign.md`, `cost-page-design.md`, `docs/provider-routing-cost-plan.md`) end-to-end
- [ ] You've spot-checked at least 2 of the per-phase briefs for technical soundness
- [ ] You agree the defensive fix bundle is the right first move (or have a better proposal)
- [ ] You've confirmed the parallel-foundation strategy works (cost-phase-1 + routing-control-surface share no files)
- [ ] You've decided whether to rewrite or retire `cost-prereq-multi-provider-activation.md`

Then: write or spawn the defensive fix bundle brief, queue the parallel foundations, and report back to Jon with the proposed sequence.

---

**Ground rules from Jon:**

- **Plain English over jargon.** Jon is non-technical; explain in product terms not code terms.
- **One clear recommendation + worst-case framing**, not a menu of options.
- **Local-only.** No remote pushes; no GitHub PR rituals.
- **Trust but verify.** Live smokes after every merge.
- **No surprises.** If a Worker brief grows beyond its LOC cap or scope, escalate to Jon before merging.
