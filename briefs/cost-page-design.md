# Cost page — phased design plan

**Date:** 2026-06-06
**Author:** Opus 4.7 (1M) — terminal-Lead
**Companion audit:** `audits/cost-page-audit.md` (read first)
**Status:** PLAN — all design questions resolved with Jon. Per-phase Worker briefs land at `briefs/cost-phase-*.md`; this doc is the master plan they reference.

---

## North star

A dedicated page where Jon can answer, in plain English and one glance:
- **How much did I spend this month?**
- **Where did it go** (which feature, which model, which agent)?
- **What would this cost if I deployed to the cloud** (synthetic API rates + Fly.io infra projection)?
- **Where could I save** by routing some features to cheaper providers (today essentially unused)?
- **Am I on track** vs prior periods, and is anything spiking?

Accessed from the existing profile popover, **not from the left rail**. Lives as a full shell route so the multi-tab content has room to breathe.

---

## Locked decisions (2026-06-06)

| # | Decision | Rationale |
|---|---|---|
| 1 | **AI/LLM is the main page; Cloud infra is a tab** | Most spend is LLM; cloud projection is useful but secondary. |
| 2 | **Synthetic API cost is the primary lens** | "What this would cost on API" answers the deploy-to-cloud question regardless of CLI subscription. Actual CLI subscription cost shown as a smaller line for context. |
| 3 | **Three providers** — Anthropic, OpenAI, Gemini — modeled in the page | Even though OpenAI/Gemini are aspirational today, the page math works on captured token volumes. |
| 4 | **All four active uses** — Visibility + Forecast + Alerts + Budgets — **phased** | Phase 1 dashboard, Phase 2 forecast, Phase 3 alerts+budgets. Avoids noisy first version. |
| 5 | **Reference cloud host: Fly.io** for the infra-estimate tab | Fits our stack (FastAPI + Postgres + pgvector). Cloudflare can front static assets + CDN, but cannot host the backend. |
| 6 | **New unified `cost_events` table** + one-time backfill | Forward writes at call boundaries; backfill from `agent_runs` + `floating_artemis_messages` + `workflow_runs` once at migration. |
| 7 | **Hero answers "How much did I spend this month"** | Default time window: this-month-to-date vs last-month-to-date comparison. |
| 8 | **Spend broken down by source bucket** | Agents / Floating Artemis / Workflows / Pipelines / Marketing / Memory consolidation / Scout intake / Background jobs. Cleanly maps to existing source paths + a `feature_tag` we add at write time. |
| 9 | **Honest dashboard + Routing opportunities panel** | Surface the truth that ~100% spend is Anthropic; show what-if savings to nudge optimization without forcing it. |
| 10 | **Full-page shell route, not modal** | Multi-tab content (Spend / Routing / Cloud infra / Budgets) needs room. Modals are too cramped. |
| 11 | **Menu position: between Connectors and divider** | Order: Account · Settings · Connectors · **Cost** · — · Help · Sign out. Groups Cost with workspace items. |
| 12 | **Don't add Claudeck flavor** — Artemis-specific design | Borrow patterns (cards, daily chart, table) but the page is its own thing. |

---

## Information architecture (target end-state)

The page is multi-tab. Default landing = **Spend** tab.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Cost                                                                │
│                                                                      │
│  HERO: This month: $87.40 · last month: $134.20 (down 35%)          │
│  [sparkline of daily spend MTD]                                      │
│                                                                      │
│  Tabs: [ Spend ] [ Routing opportunities ] [ Cloud infra ] [ Budgets ] │
│                                                                      │
│  ╔ SPEND TAB ═══════════════════════════════════════════════════════╗ │
│  ║                                                                  ║ │
│  ║  Time window: [This month ▼]   Provider: [All ▼]                ║ │
│  ║                                                                  ║ │
│  ║  ┌─Card────────┐ ┌─Card────────┐ ┌─Card────────┐ ┌─Card──────┐  ║ │
│  ║  │ This month  │ │ Today       │ │ Tokens (in) │ │ Cache     │  ║ │
│  ║  │ $87.40      │ │ $4.20       │ │ 12.3M       │ │ savings   │  ║ │
│  ║  │ vs last $134│ │ vs avg $6   │ │ Tokens (out)│ │ $18.40    │  ║ │
│  ║  │ ↓ 35%       │ │ ↓ 30%       │ │ 4.5M        │ │           │  ║ │
│  ║  └─────────────┘ └─────────────┘ └─────────────┘ └───────────┘  ║ │
│  ║                                                                  ║ │
│  ║  Spend by source bucket                Spend by model            ║ │
│  ║  ┌────────────────────────────┐       ┌──────────────────────┐  ║ │
│  ║  │ Agents          $42 (48%)  │       │ Sonnet 4.6   $58 (66%)│  ║ │
│  ║  │ Floating Artemis $18 (21%) │       │ Haiku 4.5    $22 (25%)│  ║ │
│  ║  │ Workflows        $11 (13%) │       │ Opus 4.7     $5 (6%)  │  ║ │
│  ║  │ Memory consol.   $8 (9%)   │       │ Gemini Flash $0 (0%)  │  ║ │
│  ║  │ Marketing        $5 (6%)   │       │ GPT-4o       $0 (0%)  │  ║ │
│  ║  │ Scout intake     $3 (3%)   │       │                       │  ║ │
│  ║  └────────────────────────────┘       └──────────────────────┘  ║ │
│  ║                                                                  ║ │
│  ║  Daily spend, last 30 days                                       ║ │
│  ║  ▆▅▇▆▄▃▅▄▆▇▅▄▃▄▅▆▇▆▅▄▃▂▃▄▅▆▇▆▅                              ║ │
│  ║                                                                  ║ │
│  ║  Top calls this period                                           ║ │
│  ║  ┌───────────────────────────────────────────────────────────┐  ║ │
│  ║  │ Feature         · Model     · Tokens   · Cost  · When     │  ║ │
│  ║  │ trajectory_sum  · Sonnet 4.6 · 1.2M    · $5.40 · 2h ago   │  ║ │
│  ║  │ scout_qualifier · Haiku 4.5  · 450K    · $1.20 · 5h ago   │  ║ │
│  ║  │ ...                                                       │  ║ │
│  ║  └───────────────────────────────────────────────────────────┘  ║ │
│  ╚══════════════════════════════════════════════════════════════════╝ │
└─────────────────────────────────────────────────────────────────────┘
```

### Routing opportunities tab (Phase 3)

A what-if calculator that takes captured token volumes from the last 30 days and projects savings if specified features were routed to cheaper providers.

Example output:
> **Trajectory summarizer** spent $14.20 on Sonnet 4.6 (last 30d, 4.7M tokens).
> If routed to **Gemini 2.5 Flash**: estimated $0.84/mo. **Savings: $13.36/mo.**
> ⚠ Trade-off: Flash may have weaker structured-output adherence; recommend trial run.

For each high-spend feature, the panel shows the top 1-2 alternative providers with savings + a "trade-offs" note. Configuration of actual routing is out of scope — this panel is the recommendation, the wiring is the follow-on.

### Cloud infra tab (Phase 4)

Projects what running this app on Fly.io would cost monthly:

- **Compute** — single Performance-2x machine (4 GB RAM, 2 vCPUs): ~$45/mo
- **Postgres** — Fly Postgres cluster (small, 4 GB volume): ~$20/mo
- **Storage** — 10 GB volume: ~$1.50/mo
- **Bandwidth** — first 160 GB free; usage above metered
- **Optional Cloudflare add-ons** — R2 storage ($0.015/GB-mo), CDN free tier, optionally Hyperdrive as a Postgres proxy

Plus the synthetic API LLM cost from the Spend tab. Total projected monthly cloud cost = infra + LLM.

This tab is read-only projections; no signup, no provisioning. Numbers update if Jon manually edits the configured machine size in a small config card.

### Forecast (Phase 5)

Monthly projection based on trailing 7-day spend extrapolated to month-end. Comparison to prior month. Updates the hero copy to add a forecast line.

### Budgets & alerts tab (Phase 6)

Configure soft thresholds per source bucket OR per model OR app-wide. When a threshold is crossed, a notification fires (in-app banner + optionally Slack/email if wired). Budgets are advisory — never hard caps.

---

## Phase plan

| Phase | Headline | Backend | LOC | Ships |
|---|---|---|---|---|
| 1 | **Foundation** — `cost_events` table + instrumentation + pricing registry + backfill | 1 new table + 1 migration + ~8 call-site touches + central `pricing.py` | ~450 + migration | First (parallel with R) |
| R | **Routing control surface** — `feature_routing_overrides` table + `provider_health` module + dedicated Routing page (profile menu) | 2 new tables + 2 migrations + resolver patch + 7 endpoints | ~520 + 2 migrations | First (parallel with Phase 1) |
| 2 | **Visibility dashboard** — profile menu wiring + shell route + Spend tab (cards, breakdowns, daily chart, table) | 1 unified rollup endpoint | ~500 | 2nd |
| 3 | **Routing opportunities tab** — recommendations with Apply buttons + availability filtering | 1 endpoint (reads `cost_events` + calls health probes) | ~280 | 3rd (depends on Phase R + Phase 2) |
| 4 | **Cloud infra** tab — Fly.io reference projections | Static pricing constants + 1 small endpoint | ~180 | 4th |
| 5 | **Forecast** — monthly projection + prior-period comparison | Small math on existing endpoint | ~120 | 5th |
| 6 | **Alerts + budgets** — threshold config + notifications | 1 budgets table + 1 config endpoint + notification hook | ~280 + migration | 6th |
| **Total** | | **4 tables, 4 migrations, ~14 endpoints** | **~2330** | |

**Parallel-work note:** Phase 1 (cost_events foundation) and Phase R (routing control surface) share no files and can ship in parallel — two different Workers. Phase 2 depends only on Phase 1; Phase 3 depends on both.

### Follow-on (separate ticket, not part of cost-page work)

- `briefs/cost-prereq-multi-provider-activation.md` — **partially superseded by the routing workstream above.** Once Phase R lands the DB-backed override mechanism, this brief shrinks to "seed initial overrides for memory_consolidation + trajectory_summary + district_classifier scout via POSTs to `/api/routing/features/{tag}/override`." No code change needed; the routing page or cost tab UI does it interactively. Lead should rewrite this brief after Phase R merges.

---

## Hard constraints

These apply to every phase:

1. **All call sites that produce LLM spend write to `cost_events`** — no silent calls. Phase 1 enforces the instrumentation; Lead browser-smokes the coverage by triggering each feature and confirming a row lands.
2. **Pricing comes from one place** — `artemis/costs/pricing.py`. All four existing pricing-table locations migrate to import from there.
3. **Rate snapshots are frozen on the call row** — `cost_events.input_rate` + `output_rate` capture what was charged at the time. Historical data doesn't shift when rates change.
4. **CLI vs API is explicit** — `cost_events.provider_path = 'cli' | 'api'`. No inference at read time.
5. **No retroactive correction** — backfill at migration is one-shot; after that, only forward writes.
6. **The page is read + filter only** — no editing of historical costs, no manual cost insertion, no deletion. Budgets are configurable in Phase 6 but they don't change historical data.
7. **CLI subscription cost is informational, not authoritative** — page surfaces "what you actually paid: $X subscription" alongside the synthetic API equivalent. Synthetic is the headline.
8. **Local-only git** — all branches and commits stay local per `CLAUDE.md`.
9. **No new visual languages** — reuse existing CSS tokens; the page lives in `public/css/panels/cost.css` (new file). Cards, tables, charts use existing component primitives.
10. **Lossless audit** — once `cost_events` is written, no row is ever deleted. Same discipline as memory.

---

## Open questions — resolved

All eight resolved on 2026-06-06. See the locked-decisions table above.

---

## Process notes

- Each phase = its own Worker brief (`briefs/cost-phase-N-…md`).
- Phase 1 must merge before any other phase starts — it's the data foundation.
- Phase 2 must merge before Phases 3-6 start — they all consume the rollup endpoint.
- Phases 3, 4, 5 are independent of each other and can run in parallel after Phase 2.
- Phase 6 has its own migration (budgets table) and ships last.
- Lead browser-smokes each phase on a seeded DB before declaring done.
- All branches land locally under `worker/cost-phase-N-...`.
