# Audit + Plan brief — Provider routing & token-usage work philosophy

**Type:** AUDIT + PLAN (deliverable is a reviewed report + ranked quick-wins, NOT code changes — do not
refactor routing in this pass). **Owner:** terminal opus (analysis/judgment). Read-only investigation +
a proposal doc for Jon + Lead to review.

## Goal (Jon's framing)

Establish a deliberate **3-tier provider routing philosophy** and a **token-usage work discipline**, so load
is balanced across providers and the app is **API-billing-ready** even though it runs on subscriptions today.

Tiers:
- **Tier 1 — Claude (claude-code):** high-reasoning / quality / customer-facing work. Scout judgment,
  qualifier reasoning, content & writing agents, floating Artemis, the builder.
- **Tier 2 — Codex / OpenAI (codex CLI):** mid-tier — structured generation, code-shaped tasks, moderate
  reasoning "when we can." Also a *subscription* (no API key) → free offload capacity alongside Claude.
- **Tier 3 — Local (LM Studio) / Gemini:** trivial / high-volume grunt work — summaries, classification,
  extraction, normalization, dedup. LM Studio = zero marginal cost + zero Claude load; Gemini = cheap API.

The "even balance" goal: spread load across the Claude + Codex subscriptions + local so no single
subscription's throughput/rate cap is the bottleneck (we've already hit Claude concurrency limits).

## Current state (verified 2026-06-05 — confirm + extend)

- All API keys in `.env` are EMPTY → the app runs on **claude-code (CLI/subscription)**, NOT per-token API.
  So today's lever is **throughput/limits**, not dollars — but build the routing now for API-readiness.
- Codex adapter is **CLI/subscription** (`codex exec --json`, no key) → a second free tier.
- Agents: 18 `claude-code`, **2 `anthropic`** (⚠ these would FAIL — empty ANTHROPIC_API_KEY; flag as a
  latent bug to fix or repoint to claude-code). Models: 14 `claude-haiku-4-5`, 6 `claude-sonnet-4-6`.
- Adapters EXIST but unused: codex, lm_studio, gemini, openai, openrouter. Per-agent provider/model policy
  exists. So routing is largely **config**, not a build.

## Deliverables

1. **Full LLM call-site inventory** — not just the 20 agents. Enumerate every place the app calls a model:
   the 20 agents (scouts/qualifier/content/etc.), memory consolidation, trajectory summaries, meeting
   summaries, daily-brief assembly, scout intake/normalization, qualifier brief composer, floating Artemis,
   the builder, intel/trends, signal qualification (note: deterministic, NOT an LLM call — exclude), etc.
   For each: current provider+model, rough volume/frequency, latency sensitivity, quality sensitivity,
   customer-facing? (yes/no), and a one-line "why."
2. **Proposed tier per call site** (Tier 1/2/3 per the model above) with a one-line rationale. Be
   conservative on Tier 1 (never downgrade agent reasoning or customer-facing writing).
3. **Ranked quick-wins** — the safe, high-volume/low-stakes offloads to do FIRST (favor Codex CLI + LM Studio
   = free). For each: the call site, target tier/provider, the routing change (per-agent provider policy vs
   a default vs a small selector), risk, and how to validate quality didn't regress.
4. **Token-usage work philosophy** — a short written guideline: what tier NEW work defaults to, how to
   decide, and the rule "reasoning/quality/customer-facing → Claude; everything else → cheapest tier that
   holds quality." So the discipline outlives this audit.
5. **API-readiness note** — IF we moved to per-token API billing, a rough relative cost picture per tier and
   what the routing saves. Confirm which adapters actually work today (codex CLI present? lm_studio
   reachable on the mini? gemini needs a key?) so quick-wins are real, not theoretical.
6. **Flag the 2 `anthropic`-provider agents** (empty key → broken) — recommend repoint to claude-code.

## Constraints / notes
- Read-only + a proposal doc (`docs/provider-routing-cost-plan.md`). NO routing refactor in this pass —
  Jon + Lead pick the keep-vs-offload calls from the report first.
- Respect quality: Tier-1 work (agent reasoning, qualifier judgment, customer-facing drafts) stays on Claude
  regardless of cost. The point is to free Claude capacity, not degrade output.
- Surface anything surprising (unused adapters that don't actually work, call sites with no provider policy,
  hard-coded model choices that bypass the per-agent policy).
- This is for review — return the doc + a short exec summary of the top 3–5 quick wins.
