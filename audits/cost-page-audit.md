# Cost-tracking surface audit

**Date:** 2026-06-06
**Auditor:** Opus 4.7 (1M)
**Scope:** what cost-tracking exists in `artemis-os` today + the multi-provider reality + useful patterns from the Node prototype's cost dashboard
**Companion doc:** `briefs/cost-page-design.md` is the proposed plan

---

## TL;DR

The plumbing for an app-wide cost page is **mostly already there** — token capture is wired on both major surfaces (`agent_runs` and `floating_artemis_messages`), per-model pricing tables exist for all four sources (Anthropic, OpenAI, Gemini, Claude Code CLI), and a cost-dashboard JS file is already drawn and waiting for a backend endpoint that was never built.

Three real issues block "just turn it on":

1. **Data is fragmented** across three tables with different shapes. No unified cost log. To answer "total spend across the app" today you'd need a UNION + GROUP BY across `agent_runs` + `floating_artemis_messages` + `workflow_runs`.
2. **Multi-provider is aspirational.** All three SDK adapters (Anthropic, OpenAI, Gemini) are fully implemented and registered, but OpenAI + Gemini are **not in any default provider cascade**. Today, essentially 100% of spend is Anthropic-routed (via CLI subscription or SDK). The "divide work across providers" strategy is wired infrastructure but operationally unused.
3. **Pricing is duplicated** across four code locations and not exposed for the page to project "what would this cost on API". No single source of truth for per-model rates.

Everything else is additive: instrument the missing call sites, unify the data into a `cost_events` write-ahead table, expose a rollup endpoint, wire a new "Cost" item into the existing profile popover. The cost page itself is mostly a frontend design problem on top of clean data.

---

## 1. What cost data exists today

### Token capture per call

**Agent runs (pipeline path)** — `agent_runs` table:
- Columns: `cost_input_tokens` (BigInt), `cost_output_tokens` (BigInt)
- Captured at call time from the Anthropic SDK's `usage` object (`artemis/builders/executor.py:423-424`)
- Cost computed after-the-fact via blended Sonnet rates (`artemis/memory/repository.py:670-671`)

**Floating Artemis (chat path)** — `floating_artemis_messages` table:
- Columns: `cost_input_tokens`, `cost_output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
- Captured per message (`artemis/floating_artemis/chat.py:1069-1072`)
- Cache tokens tracked separately so prompt-caching savings are visible

**Workflows** — `workflow_runs` table:
- Column: `total_cost_usd` (Float) — already computed per workflow run

**Tool invocations** — `tool_invocations` table:
- Counts per agent_run_id or builder_session_id, no cost attribution today

### What's NOT captured per call

- Provider/CLI flag (we can infer from the adapter used, but it's not explicit)
- Model ID at the call site (`agent_runs` doesn't store it; agents store model on the `agents` table)
- Feature/source label (we can derive from which table the row is in, but it's a blunt instrument)
- Cache breakdown on `agent_runs` (only on FA messages)
- Latency / duration (could be derived from `started_at`/`completed_at` where present)

### Per-model pricing definitions (4 separate locations)

1. **Anthropic** — `artemis/builders/_cost.py:19-30`
   - Hard-coded: Opus 4.7 ($15/$75), Sonnet 4.6 ($3/$15), Haiku 4.5 ($0.80/$4)
   - Prefix-based fallback for unknown versions

2. **OpenAI** — `artemis/providers/openai/models.py:29-41`
   - Per-1k-token rates: gpt-4o ($0.0025/$0.01), gpt-5-mini ($0.00025/$0.002), etc.

3. **Gemini** — `artemis/providers/gemini/models.py:21-27`
   - Per-1k-token rates: 1.5-pro ($0.00125/$0.005), 2.5-flash ($0.00015/$0.0006)

4. **Claude Code CLI** — `artemis/providers/claude_code/adapter.py:174-178`
   - No rate definition; CLI is subscription-based. Usage is extracted from CLI JSON output when present, else zero. Cost = subscription is flat, not per-token.

**Problem:** four files to update when rates change. The cost page would benefit from a single `artemis/costs/pricing.py` registry; the four locations migrate to it.

### Existing cost surfaces in the UI

1. **Status bar cost display** (`public/js/ui/status-bar.js`)
   - Shows `sb-session-cost` and `sb-total-cost` numbers
   - Click opens a cost dashboard

2. **Context gauge** (`public/js/ui/context-gauge.js`)
   - Per-session token breakdown popup: total, input, output, cache read, cache write

3. **Cost dashboard JS** (`public/js/features/cost-dashboard.js`, ~156 LOC)
   - Already renders cards + daily chart + session table
   - Calls `/api/stats/dashboard` — **this endpoint does not exist in the backend**
   - It's dormant frontend waiting for a backend. Half the work for Phase 2 is already drawn.

4. **Agent-metrics endpoint** — `/api/stats/agent-metrics` (`artemis/routes/stats.py:142-152` returns the real data)
   - Per-agent rollup: total_cost, total_input_tokens, total_output_tokens, last_run_at
   - Aggregates `agent_runs` only — doesn't include FA or workflows

5. **Workflow cost** — `workflow_runs.total_cost_usd` is computed but **no aggregation endpoint exists** that sums across workflows over time

### CLI vs API distinction

- `provider_id == "claude-code"` identifies CLI calls (`artemis/routes/stats.py:72`)
- `AnthropicAdapter` (direct SDK) returns full Usage object with input + output + cache breakdown
- `ClaudeCodeAdapter` extracts usage from CLI JSON where present; falls back to zeros

**Bottom line:** we *can* tell which calls are CLI vs API, but the distinction isn't explicit metadata on any row today — we'd have to infer from adapter choice. A `cost_events` table should record this explicitly so projections and "what would API cost" math are unambiguous.

### Run-level vs call-level rollups

- **Per-agent-run** — summed at call boundary into `agent_runs.cost_input_tokens`/`cost_output_tokens`
- **Per-FA-message** — captured per message; **no per-session rollup endpoint**
- **Per-workflow-run** — `workflow_runs.total_cost_usd` per run; **no aggregation endpoint**
- **Per-day / per-week / per-month** — no time-series rollups anywhere. Would require app-level date arithmetic on every page load.

---

## 2. Multi-provider reality

This is the big surprise. The intent was Anthropic-for-critical, Gemini-and-OpenAI-for-cheap, with the ability to swap providers per feature. Reality:

### Wired infrastructure

- **All three SDK adapters fully implemented and registered:**
  - Anthropic: `artemis/agent/client.py:37` — uses official `anthropic` SDK
  - OpenAI: `artemis/providers/openai/adapter.py` — pure httpx (no SDK), full streaming + tool calling + o-series support
  - Gemini: `artemis/providers/gemini/adapter.py` — pure httpx, full streaming + tool calling
- **Claude Code CLI** also wired: `artemis/providers/claude_code/adapter.py` — preferred fallback for cost reasons
- **Single resolution layer** at `artemis/providers/resolver.py` — `resolve_adapter(provider, fallback_provider)` walks a cascade

### What's broken (or never connected)

- **Default cascade is Anthropic-only at the end:** `("claude-code", "codex", "lm-studio", "anthropic")` — OpenAI and Gemini are NOT in the default cascade.
- **Hard-coded provider in critical paths:**
  - Memory consolidator → `resolve_adapter(provider="claude-code")` (`artemis/memory/consolidator.py:168`)
  - Meeting summarizer cascade → `("claude-code", "codex", "lm-studio", "anthropic")` (`artemis/meetings/summarizer.py:290`)
  - Brief generator cascade → same shape (`artemis/brief/generator.py:85`)
  - Trajectory summarizer → same shape (`artemis/builder/trajectory_summarizer.py:180`)
- **Per-agent provider columns exist** but rarely populated. `agents.provider` and `agents.fallback_provider` can route to OpenAI/Gemini, but few agents are configured to do so.
- **MCP sandbox** has the broadest cascade including OpenAI + Gemini (`artemis/tools/mcp_server.py:290`), so sub-execution of agent tools is the only place a feature ever actually reaches a non-Anthropic provider in practice.
- **Floating Artemis** is the only feature where a user can explicitly pick Gemini or OpenAI from the UI (model picker per session). That's the only first-party path that routinely reaches non-Anthropic.

### What this means for the cost page

Today's spend is essentially 100% Anthropic — split between CLI subscription (free at the per-call level, paid as flat subscription) and direct SDK calls (real per-token API spend).

The "Routing opportunities" panel proposed in the design doc is **what-if math**: "you spent N tokens on trajectory summarization at Sonnet rates; if you routed that to Gemini Flash at its rates, savings = $X/mo." That math works regardless of whether the routing is actually wired today.

But for the panel to translate into actual savings, the multi-provider routing has to become live. That's a separate follow-on, scoped in `briefs/cost-prereq-multi-provider-activation.md`.

---

## 3. Patterns to keep + drop from the Node prototype

The Node prototype's cost dashboard (`claudeck-artemis/public/js/features/cost-dashboard.js`, 156 LOC) is lean and honest. It answers "what did I spend" without trying to do forecasting or budgets.

### Keep

- **4-card summary row** — Total · Project · Today · Tokens. Visually scannable; quick KPI snapshot. (Adapt to: Total · This Month · Today · Cache savings.)
- **Session-level detail table with sortable columns** — easy client-side scan.
- **Daily bar chart normalized to max** — horizontal bars, no external charting lib. Space-efficient.
- **Project-scoped queries from the start** — the Node version passes `project_path` into every endpoint; results filter at the SQL layer. Artemis should mirror this for scope/agent/workflow filters.
- **Token tracking alongside cost** — showing "12.3M in / 4.5M out" alongside dollar amounts helps you see volume vs cost trade-offs.
- **Cost recording at the call boundary** — single `addCost()` call per turn; clean audit trail.
- **Prepared statement queries with a lightweight wrapper** — no ORM overhead; SQL stays visible.

### Drop

- **Hard-coded 30-day window** — no date range picker. We want dynamic ranges.
- **Pricing rates baked into provider code** — Node hard-codes them in adapters. We're moving to a single `artemis/costs/pricing.py` registry.
- **No model or provider breakdown** — Node doesn't answer "which model cost the most?" Critical for Artemis given the multi-provider story.
- **No cache-efficiency visualization** — cache tokens captured but never shown. Should be a metric card.
- **Session title as the only identifier** — often empty or identical. We'll include feature, model, agent name, run ID.
- **No alerts or budget enforcement** — Node doesn't warn on spikes or forecast runaway. Phase 3 of the redesign adds this.

---

## 4. What the page needs (gap analysis)

Mapping the audit to design requirements:

| Need | Backend status today | Frontend status today | Phase that delivers |
|---|---|---|---|
| Unified cost log per call | None — fragmented across 3 tables | None | Phase 1 (foundation) |
| Single pricing source of truth | 4 separate files | N/A | Phase 1 (foundation) |
| Backfill historical data | N/A | N/A | Phase 1 (foundation) |
| Total spend dashboard endpoint | Missing (`/api/stats/dashboard` orphaned) | JS exists but unwired | Phase 2 (visibility) |
| Profile menu "Cost" entry | N/A | Profile popover at `artemis-shell.js:541` has no Cost item | Phase 2 (visibility) |
| Source-bucket breakdown | Not implemented | Not implemented | Phase 2 (visibility) |
| Model breakdown | Pricing tables exist; no aggregation | Not implemented | Phase 2 (visibility) |
| Daily/weekly/monthly time series | Not implemented | Not implemented | Phase 2 (visibility) |
| Cache savings visualization | FA captures cache tokens; agents don't | Not implemented | Phase 2 (visibility) |
| Routing opportunities ("what if") | Pricing math available | Not implemented | Phase 3 (routing) |
| Cloud infra projection | Out of scope today | Out of scope today | Phase 4 (cloud tab) |
| Monthly forecast | Not implemented | Not implemented | Phase 5 (forecast) |
| Alerts at thresholds | Not implemented | Not implemented | Phase 6 (alerts) |
| Per-feature budgets | Not implemented | Not implemented | Phase 6 (alerts) |

---

## 5. What's NOT in scope for the cost page

To keep the redesign honest:

- **No write surface from the page** — actions on the cost page are read + filter + configure-budget. We don't change billing from there.
- **No retroactive cost correction** — if rates change, future calls use new rates; historical `cost_events` rows are frozen with the rate snapshot at the time of the call.
- **No subscription attribution math** — Claude Max is a flat subscription. We surface "what you actually pay this month: $200 subscription" as a static line; we don't try to allocate it per call. The synthetic API equivalent is the primary lens.
- **No competitor pricing benchmarks** beyond the three providers the app already supports.

---

## 6. Summary

The cost page is mostly an **exposure + unification** job, not a from-scratch build. Tokens are already captured, rates are already known, half the frontend is already drawn. Phase 1 unifies data; Phase 2 makes it visible; Phases 3-6 add depth (routing, infra, forecast, alerts).

The multi-provider gap is real but doesn't block the page — what-if math works on captured token volumes regardless of which provider was used. Activating the routing strategy is a separate follow-on.

Companion plan: `briefs/cost-page-design.md`.
